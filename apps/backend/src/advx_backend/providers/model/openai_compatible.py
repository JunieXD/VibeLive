import asyncio
import base64
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final, cast
from urllib.parse import quote, urlsplit

import httpx

from advx_backend.application.ports.ingest import FrameResolver
from advx_backend.contracts.generation import (
    BarrageCandidate,
    FrameRef,
    GenerationRequest,
    GenerationResult,
)
from advx_backend.domain.observation import FrameRef as DomainFrameRef
from advx_backend.providers.model.base import (
    CapabilityProbeCheck,
    CapabilityProbeResult,
    CapabilityProbeStatus,
)


class OpenAICompatibleProviderError(RuntimeError):
    """Normalized failure raised by the OpenAI-compatible model adapter."""


class OpenAICompatibleClosedError(OpenAICompatibleProviderError):
    """Raised when a request is made after the provider has been closed."""


class OpenAICompatibleTimeoutError(OpenAICompatibleProviderError):
    """Raised when an upstream request exceeds the configured timeout."""


class OpenAICompatibleTransportError(OpenAICompatibleProviderError):
    """Raised when the configured endpoint cannot be reached."""


class OpenAICompatibleHttpError(OpenAICompatibleProviderError):
    """Raised when the upstream endpoint returns a non-success status."""

    def __init__(
        self,
        status_code: int,
        *,
        retry_after_seconds: float | None = None,
        response: httpx.Response | None = None,
    ) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.response = response
        super().__init__(f"OpenAI-compatible provider returned HTTP {status_code}")


class OpenAICompatibleProtocolError(OpenAICompatibleProviderError):
    """Raised when a response does not follow the expected Chat Completions shape."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "invalid_upstream_response",
    ) -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    request_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("OpenAI-compatible base URL is required")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("OpenAI-compatible model is required")
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("OpenAI-compatible API key is required")
        if (
            not isinstance(self.request_timeout_seconds, (int, float))
            or isinstance(self.request_timeout_seconds, bool)
            or not math.isfinite(self.request_timeout_seconds)
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("request timeout must be a positive finite number")

        base_url = self.base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenAI-compatible base URL must be an HTTP(S) origin or path")

        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", self.model.strip())


_SYSTEM_PROMPT: Final = (
    "Generate concise audience barrage candidates for a live room. "
    "Use only audience_id values supplied in the input. "
    "Return exactly one JSON object, with no Markdown or prose. "
    'Use this shape: {"candidates":[{"audience_id":"audience-id","text":"弹幕内容"}]}.'
)
_PROBE_IMAGE: Final = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAaklEQVR4nO3PAQ2AMADAsMtB"
    "xOUgDGGIQcXZnzSZgHVc93N0Iz8AqA8A6gOA+gCgPgCoDwDqA4D64G/AO+fSAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAD2A+wWQB1AHUAdQB1AHUAdQN3xgA90/GEe0tjTVAAAAABJ"
    "RU5ErkJggg=="
)
_BLOCKING_HTTP_STATUSES: Final = frozenset({401, 402, 403, 408, 429})
_PROBE_OUTPUT_TOKEN_BUDGET: Final = 4_096
_STEPFUN_API_HOST: Final = "api.stepfun.com"
_STEPFUN_REASONING_MODEL: Final = "step-3.7-flash"
JSON_MODE_RESPONSE_FORMAT: Final = {"type": "json_object"}


def default_reasoning_options(base_url: str, model_id: str) -> dict[str, str]:
    """Return supported low-latency defaults for known reasoning endpoints."""

    if (
        urlsplit(base_url).hostname == _STEPFUN_API_HOST
        and model_id.strip() == _STEPFUN_REASONING_MODEL
    ):
        return {"reasoning_effort": "low"}
    return {}


class OpenAICompatibleProvider:
    """Non-streaming OpenAI Chat Completions adapter for barrage generation.

    The adapter owns a client it creates itself. Callers that inject a client
    retain ownership of that client and can close it independently.
    """

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient | None = None,
        frame_resolver: FrameResolver | None = None,
    ) -> None:
        self.config = config
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None
        self._frame_resolver = frame_resolver
        self._inflight: dict[str, asyncio.Task[object]] = {}
        self._inflight_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def health(self) -> bool:
        """Check that the configured model endpoint accepts authenticated requests."""

        try:
            await self._send("GET", self._model_endpoint())
        except OpenAICompatibleProviderError:
            return False
        return True

    async def discover_models(self) -> tuple[str, ...]:
        """Return bounded model IDs from the authenticated OpenAI-compatible catalog."""

        response = await self._send("GET", self._models_endpoint())
        payload = self._json_object(response.content, "models response")
        data = payload.get("data")
        if not isinstance(data, list):
            raise OpenAICompatibleProtocolError("models response must contain a data array")

        model_ids: list[str] = []
        for item in data[:1_000]:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and 0 < len(model_id) <= 256:
                model_ids.append(model_id)
        return tuple(dict.fromkeys(model_ids))

    async def probe_capabilities(
        self,
        *,
        role_models: dict[str, str],
    ) -> CapabilityProbeResult:
        """Probe only minimal, non-streaming requests and return redacted outcomes."""

        try:
            discovered_model_ids = await self.discover_models()
            discovery_check = CapabilityProbeCheck(
                capability="model_discovery",
                status=CapabilityProbeStatus.PASSED,
            )
        except asyncio.CancelledError:
            raise
        except OpenAICompatibleProviderError as error:
            discovered_model_ids = ()
            discovery_check = self.normalize_probe_error("model_discovery", None, error)

        structured_roles = ("viewer", "memory")
        structured_checks = await asyncio.gather(
            *(
                self._probe_chat(
                    capability=f"{role}_json_output",
                    model_id=role_models[role],
                )
                for role in structured_roles
            )
        )
        image_check = await self._probe_chat(
            capability="image_input",
            model_id=role_models["visual_summary"],
            include_image=True,
        )
        viewer_checks = await asyncio.gather(
            *(
                self._probe_chat(
                    capability=f"viewer_concurrency_{index}",
                    model_id=role_models["viewer"],
                )
                for index in (1, 2)
            )
        )
        concurrency_status = self._overall_status(viewer_checks)
        concurrency_check = CapabilityProbeCheck(
            capability="viewer_minimal_concurrency",
            status=concurrency_status,
            model_id=role_models["viewer"],
            error_code=next(
                (check.error_code for check in viewer_checks if check.error_code is not None),
                None,
            ),
            http_status=next(
                (check.http_status for check in viewer_checks if check.http_status is not None),
                None,
            ),
        )
        required_text_checks = (
            discovery_check,
            *structured_checks,
            concurrency_check,
        )
        if (
            image_check.status is CapabilityProbeStatus.FAILED
            and image_check.http_status == 400
            and all(
                check.status is CapabilityProbeStatus.PASSED
                for check in required_text_checks
            )
        ):
            image_check = CapabilityProbeCheck(
                capability=image_check.capability,
                status=CapabilityProbeStatus.SKIPPED,
                model_id=image_check.model_id,
                error_code="image_input_unsupported",
                http_status=image_check.http_status,
            )
        checks = (
            discovery_check,
            *structured_checks,
            image_check,
            concurrency_check,
        )
        return CapabilityProbeResult(
            status=self._overall_status(checks),
            discovered_model_ids=discovered_model_ids,
            checks=checks,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        task = await self._register_request(request.request_id)
        try:
            response = await self._send(
                "POST",
                self._chat_completions_endpoint(),
                payload=await self._request_payload(request),
            )
            candidates = self._parse_candidates(response)
            # The upstream payload is untrusted; this correlation id is local.
            return GenerationResult(request_id=request.request_id, candidates=candidates)
        finally:
            async with self._inflight_lock:
                if self._inflight.get(request.request_id) is task:
                    del self._inflight[request.request_id]

    async def cancel(self, request_id: str) -> None:
        """Cancel only the task currently associated with ``request_id``."""

        async with self._inflight_lock:
            task = self._inflight.get(request_id)
        if task is not None:
            task.cancel()

    async def aclose(self) -> None:
        """Cancel active requests and close an internally created HTTP client once."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True

            async with self._inflight_lock:
                tasks = tuple(self._inflight.values())
            current_task = asyncio.current_task()
            pending = tuple(task for task in tasks if task is not current_task)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if self._owns_client:
                await self._client.aclose()

    async def close(self) -> None:
        """Compatibility alias for ``aclose``."""

        await self.aclose()

    async def _register_request(self, request_id: str) -> asyncio.Task[object]:
        task = asyncio.current_task()
        if task is None:
            raise OpenAICompatibleProtocolError("generation must run in an asyncio task")

        async with self._inflight_lock:
            self._ensure_open()
            active = self._inflight.get(request_id)
            if active is not None and not active.done():
                raise OpenAICompatibleProtocolError("generation request id is already active")
            self._inflight[request_id] = task
        return task

    async def _request_payload(self, request: GenerationRequest) -> dict[str, object]:
        context = {
            "observation": {
                "session_id": request.observation.session_id,
                "observation_id": request.observation.observation_id,
                "created_at_ms": request.observation.created_at_ms,
                "room_events": [
                    event.model_dump(mode="json") for event in request.observation.room_events
                ],
                "user_context": dict(request.observation.user_context),
            },
            "audiences": [audience.model_dump(mode="json") for audience in request.audiences],
        }
        try:
            context_text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            raise OpenAICompatibleProtocolError(
                "generation request cannot be encoded as JSON"
            ) from None

        content: str | list[dict[str, object]] = context_text
        image_parts = await self._image_parts(
            request.observation.session_id,
            request.observation.frames,
        )
        if image_parts:
            content = [{"type": "text", "text": context_text}, *image_parts]

        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "stream": False,
            "n": 1,
            "response_format": JSON_MODE_RESPONSE_FORMAT,
        }
        payload.update(default_reasoning_options(self.config.base_url, self.config.model))
        return payload

    async def _image_parts(
        self,
        session_id: str,
        frames: list[FrameRef],
    ) -> list[dict[str, object]]:
        if self._frame_resolver is None:
            return []

        image_parts: list[dict[str, object]] = []
        for frame in frames:
            try:
                resolved = await self._frame_resolver.resolve(
                    session_id=session_id,
                    frame=cast(DomainFrameRef, frame),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                raise OpenAICompatibleProtocolError("frame resolution failed") from None
            if resolved is None:
                continue
            if resolved.session_id != session_id or resolved.frame_id != frame.frame_id:
                raise OpenAICompatibleProtocolError("frame resolver returned a mismatched frame")
            if resolved.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise OpenAICompatibleProtocolError(
                    "frame resolver returned an unsupported image type"
                )
            encoded = base64.b64encode(resolved.body).decode("ascii")
            image_url = f"data:{resolved.mime_type};base64,{encoded}"
            image_parts.append({"type": "image_url", "image_url": {"url": image_url}})
        return image_parts

    async def _send(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> httpx.Response:
        self._ensure_open()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        response = await self._request(method, url, headers=headers, payload=payload)

        if self._should_fallback_from_json_mode(response, payload):
            fallback_payload = dict(payload)
            fallback_payload.pop("response_format", None)
            response = await self._request(method, url, headers=headers, payload=fallback_payload)

        if not response.is_success:
            raise OpenAICompatibleHttpError(
                response.status_code,
                retry_after_seconds=self._retry_after_seconds(response.headers.get("Retry-After")),
                response=response,
            )
        return response

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object] | None,
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=self.config.request_timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError):
            raise OpenAICompatibleTimeoutError("OpenAI-compatible request timed out") from None
        except (httpx.HTTPError, RuntimeError):
            raise OpenAICompatibleTransportError("OpenAI-compatible transport failed") from None

    @staticmethod
    def _should_fallback_from_json_mode(
        response: httpx.Response,
        payload: dict[str, object] | None,
    ) -> bool:
        if response.status_code != 400 or payload is None:
            return False
        if payload.get("response_format") != JSON_MODE_RESPONSE_FORMAT:
            return False
        try:
            detail = response.text.lower()
        except (UnicodeDecodeError, httpx.HTTPError):
            return False
        return "response_format" in detail and (
            "json_object" in detail or "json mode" in detail
        )

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, IndexError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return seconds

    async def _probe_chat(
        self,
        *,
        capability: str,
        model_id: str,
        include_image: bool = False,
    ) -> CapabilityProbeCheck:
        content: str | list[dict[str, object]] = (
            'Return exactly this JSON object and no Markdown or prose: {"ok":true}.'
        )
        if include_image:
            content = [
                {
                    "type": "text",
                    "text": (
                        'Return exactly this JSON object and no Markdown or prose: {"ok":true}.'
                    ),
                },
                {"type": "image_url", "image_url": {"url": _PROBE_IMAGE}},
            ]
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "n": 1,
            "response_format": JSON_MODE_RESPONSE_FORMAT,
            # Match the production role budget so reasoning-capable multimodal
            # models can finish before emitting the tiny structured response.
            "max_tokens": _PROBE_OUTPUT_TOKEN_BUDGET,
        }
        payload.update(default_reasoning_options(self.config.base_url, model_id))
        try:
            response = await self._send(
                "POST",
                self._chat_completions_endpoint(),
                payload=payload,
            )
            self._parse_probe_response(response)
        except asyncio.CancelledError:
            raise
        except OpenAICompatibleProviderError as error:
            return self.normalize_probe_error(capability, model_id, error)
        return CapabilityProbeCheck(
            capability=capability,
            status=CapabilityProbeStatus.PASSED,
            model_id=model_id,
        )

    def _parse_probe_response(self, response: httpx.Response) -> None:
        payload = self._json_object(response.content, "probe response")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise OpenAICompatibleProtocolError("probe response must contain a choice")
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise OpenAICompatibleProtocolError(
                "probe response exhausted its output token budget",
                error_code="output_token_limit",
            )
        if finish_reason not in {None, "stop"}:
            raise OpenAICompatibleProtocolError("probe response did not finish normally")
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OpenAICompatibleProtocolError("probe response must contain JSON text")
        output = self._json_object(cast(str, message["content"]), "probe structured output")
        if output != {"ok": True}:
            raise OpenAICompatibleProtocolError("probe structured output did not match schema")

    @staticmethod
    def normalize_probe_error(
        capability: str,
        model_id: str | None,
        error: OpenAICompatibleProviderError,
    ) -> CapabilityProbeCheck:
        http_status = error.status_code if isinstance(error, OpenAICompatibleHttpError) else None
        if isinstance(error, OpenAICompatibleHttpError):
            error_code = "upstream_http_error"
            status = (
                CapabilityProbeStatus.BLOCKED
                if error.status_code in _BLOCKING_HTTP_STATUSES
                else CapabilityProbeStatus.FAILED
            )
        elif isinstance(error, OpenAICompatibleTimeoutError):
            error_code = "upstream_timeout"
            status = CapabilityProbeStatus.BLOCKED
        elif isinstance(error, OpenAICompatibleTransportError):
            error_code = "upstream_unreachable"
            status = CapabilityProbeStatus.BLOCKED
        elif isinstance(error, OpenAICompatibleClosedError):
            error_code = "provider_closed"
            status = CapabilityProbeStatus.FAILED
        elif isinstance(error, OpenAICompatibleProtocolError):
            error_code = error.error_code
            status = CapabilityProbeStatus.FAILED
        else:
            error_code = "invalid_upstream_response"
            status = CapabilityProbeStatus.FAILED
        return CapabilityProbeCheck(
            capability=capability,
            status=status,
            model_id=model_id,
            error_code=error_code,
            http_status=http_status,
        )

    @staticmethod
    def _overall_status(
        checks: tuple[CapabilityProbeCheck, ...] | list[CapabilityProbeCheck],
    ) -> CapabilityProbeStatus:
        statuses = {check.status for check in checks}
        if CapabilityProbeStatus.BLOCKED in statuses:
            return CapabilityProbeStatus.BLOCKED
        if CapabilityProbeStatus.FAILED in statuses:
            return CapabilityProbeStatus.FAILED
        if statuses == {CapabilityProbeStatus.SKIPPED}:
            return CapabilityProbeStatus.SKIPPED
        return CapabilityProbeStatus.PASSED

    def _parse_candidates(self, response: httpx.Response) -> list[BarrageCandidate]:
        payload = self._json_object(response.content, "response")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise OpenAICompatibleProtocolError("response must contain exactly one choice")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise OpenAICompatibleProtocolError("response choice must be an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise OpenAICompatibleProtocolError("response choice must contain a message object")
        content = message.get("content")
        if not isinstance(content, str):
            raise OpenAICompatibleProtocolError("response message must contain JSON text")

        output = self._json_object(content, "structured output")
        raw_candidates = output.get("candidates")
        if not isinstance(raw_candidates, list):
            raise OpenAICompatibleProtocolError("structured output must contain a candidates array")
        return [self._candidate(candidate) for candidate in raw_candidates]

    @staticmethod
    def _candidate(value: object) -> BarrageCandidate:
        if not isinstance(value, dict):
            raise OpenAICompatibleProtocolError("candidate must be an object")
        if set(value) != {"audience_id", "text"}:
            raise OpenAICompatibleProtocolError("candidate fields must be audience_id and text")

        audience_id = value["audience_id"]
        text = value["text"]
        if not isinstance(audience_id, str) or not audience_id:
            raise OpenAICompatibleProtocolError("candidate audience_id must be a non-empty string")
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise OpenAICompatibleProtocolError(
                "candidate text must be a non-empty string up to 200 characters"
            )
        return BarrageCandidate(audience_id=audience_id, text=text)

    @staticmethod
    def _json_object(value: bytes | str, source: str) -> dict[str, object]:
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise OpenAICompatibleProtocolError(
                f"OpenAI-compatible provider returned invalid {source} JSON"
            ) from None
        if not isinstance(parsed, dict):
            raise OpenAICompatibleProtocolError(
                f"OpenAI-compatible provider returned a non-object {source}"
            )
        return parsed

    def _chat_completions_endpoint(self) -> str:
        return f"{self.config.base_url}/chat/completions"

    def _model_endpoint(self) -> str:
        return f"{self.config.base_url}/models/{quote(self.config.model, safe='')}"

    def _models_endpoint(self) -> str:
        return f"{self.config.base_url}/models"

    def _ensure_open(self) -> None:
        if self._closed:
            raise OpenAICompatibleClosedError("OpenAI-compatible provider is closed")
