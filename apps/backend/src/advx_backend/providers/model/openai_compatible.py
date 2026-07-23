import asyncio
import json
import math
from dataclasses import dataclass, field
from typing import Final, Protocol
from urllib.parse import quote, urlsplit

import httpx

from advx_backend.contracts.generation import (
    BarrageCandidate,
    FrameRef,
    GenerationRequest,
    GenerationResult,
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

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"OpenAI-compatible provider returned HTTP {status_code}")


class OpenAICompatibleProtocolError(OpenAICompatibleProviderError):
    """Raised when a response does not follow the expected Chat Completions shape."""


class FrameResolver(Protocol):
    """Resolves an in-memory frame reference to an image URL for one request."""

    async def resolve(self, frame: FrameRef) -> str | None: ...


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
    "Return only the JSON object required by the response schema."
)
_CANDIDATE_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["audience_id", "text"],
                "properties": {
                    "audience_id": {"type": "string", "minLength": 1},
                    "text": {"type": "string", "minLength": 1, "maxLength": 200},
                },
            },
        },
    },
}


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
        image_parts = await self._image_parts(request.observation.frames)
        if image_parts:
            content = [{"type": "text", "text": context_text}, *image_parts]

        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "stream": False,
            "n": 1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "barrage_candidates",
                    "strict": True,
                    "schema": _CANDIDATE_SCHEMA,
                },
            },
        }

    async def _image_parts(self, frames: list[FrameRef]) -> list[dict[str, object]]:
        if self._frame_resolver is None:
            return []

        image_parts: list[dict[str, object]] = []
        for frame in frames:
            try:
                image_url = await self._frame_resolver.resolve(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise OpenAICompatibleProtocolError("frame resolution failed") from None
            if image_url is None:
                continue
            if not isinstance(image_url, str) or not image_url:
                raise OpenAICompatibleProtocolError("frame resolver returned an invalid image URL")
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

        try:
            response = await self._client.request(
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

        if not response.is_success:
            raise OpenAICompatibleHttpError(response.status_code)
        return response

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

    def _ensure_open(self) -> None:
        if self._closed:
            raise OpenAICompatibleClosedError("OpenAI-compatible provider is closed")
