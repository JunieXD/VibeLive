import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import Final, Protocol, cast

import httpx
from pydantic import BaseModel, ValidationError

from advx_backend.application.ai_call_logging import (
    AiCallLifecycle,
    AiCallScope,
    AiCallSink,
    build_http_response_summary,
    build_openai_request_summary,
)
from advx_backend.application.ports.ingest import FrameResolver
from advx_backend.contracts.debug import AiCallRole
from advx_backend.contracts.viewer_runtime import (
    ProviderRuntimeSpec,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
)
from advx_backend.domain.observation import FrameRef
from advx_backend.domain.observation_wave import (
    FrameBundle,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleHttpError,
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
    OpenAICompatibleTimeoutError,
    OpenAICompatibleTransportError,
    default_reasoning_options,
)


class ViewerRuntimeProviderError(RuntimeError):
    """Normalized failure from the role-aware runtime model adapter."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class ViewerRuntimeProviderBlockedError(ViewerRuntimeProviderError):
    """Raised when a live role call cannot run without configured credentials."""


class ViewerRuntimeProtocolError(ViewerRuntimeProviderError):
    """Raised when a role response violates its strict runtime contract."""

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str | None = None,
        token_budget: int | None = None,
    ) -> None:
        self.finish_reason = finish_reason
        self.token_budget = token_budget
        super().__init__(message)


class VisualSummaryProvider(Protocol):
    async def summarize(
        self,
        wave: ObservationWave,
        frame_bundle: FrameBundle,
        runtime: object,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class OpenAICompatibleViewerRuntimeConfig:
    base_url: str
    provider: ProviderRuntimeSpec
    api_key: str | None = field(default=None, repr=False)
    request_timeout_seconds: float = 30.0


_VIEWER_BARRAGE_JSON_EXAMPLE: Final = (
    '{"generation_request_id":"request-id","viewer_instance_id":"viewer-id",'
    '"viewer_sequence":1,"action":"barrage","intent":"react_to_host",'
    '"target":null,"text":"这波漂亮","reaction_type":"comment",'
    '"evidence_refs":[]}'
)
_VIEWER_SILENCE_JSON_EXAMPLE: Final = (
    '{"generation_request_id":"request-id","viewer_instance_id":"viewer-id",'
    '"viewer_sequence":1,"action":"silence","intent":"silence",'
    '"target":null,"text":null,"reaction_type":"silence","evidence_refs":[]}'
)
_SUMMARY_JSON_EXAMPLE: Final = '{"summary":"画面中的关键变化"}'
_VIEWER_SYSTEM_PROMPT: Final = (
    "Act as exactly the supplied viewer instance. The username is your identity; the Persona "
    "is only a behavioral tendency and is not your name or a system role. Produce zero or one "
    "natural barrage reaction. You may react to the host, scene, or a replyable public Viewer "
    "event, but may target only IDs explicitly allowed by the request. Shared room memory is "
    "public background, not proof that you personally attended an earlier stream. Use only "
    "evidence references present in the input. Prefer a natural Chinese message of "
    "20 characters or fewer. Return exactly one JSON object, with no Markdown or prose. "
    f"For a barrage use this shape: {_VIEWER_BARRAGE_JSON_EXAMPLE} "
    f"For no response use this shape: {_VIEWER_SILENCE_JSON_EXAMPLE} "
    "For a host, scene, or room target, viewer_instance_id and event_id must both be null. "
    "For a viewer target, provide viewer_instance_id only; for an event target, provide event_id "
    "only. Never use an empty string for text."
)
_VISUAL_SUMMARY_SYSTEM_PROMPT: Final = (
    "Summarize only visible, decision-relevant changes across the ordered frame bundle. "
    "Do not invent events or identities. Return exactly one JSON object, with no Markdown "
    f"or prose. Use this shape: {_SUMMARY_JSON_EXAMPLE}"
)
_HISTORY_SUMMARY_SYSTEM_PROMPT: Final = (
    "Compress the supplied earlier live-room history into a factual chronological "
    "summary. Preserve names, direct questions, unresolved requests, important game "
    "events, agreements, disagreements, and running context. Do not invent details. Return "
    f"exactly one JSON object, with no Markdown or prose. Use this shape: {_SUMMARY_JSON_EXAMPLE}"
)
_ROLE_OUTPUT_TOKEN_BUDGET: Final = 4_096


class OpenAICompatibleViewerRuntimeProvider:
    """Per-viewer model adapter for one active provider profile."""

    def __init__(
        self,
        config: OpenAICompatibleViewerRuntimeConfig,
        *,
        client: httpx.AsyncClient | None = None,
        frame_resolver: FrameResolver | None = None,
        ai_call_sink: AiCallSink | None = None,
    ) -> None:
        self.config = config
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None
        self._frame_resolver = frame_resolver
        self._ai_call_sink = ai_call_sink
        self._viewer = self._role_provider(config.provider.viewer_model)
        self._visual_summary = self._role_provider(config.provider.visual_summary_model)
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def generate(self, request: ViewerGenerationRequest) -> ViewerGenerationResponse:
        lifecycle = self._call_lifecycle(
            role=AiCallRole.VIEWER,
            correlation_id=request.generation_request_id,
            model_id=self.config.provider.viewer_model,
            scope=AiCallScope(
                room_id=request.room_id,
                session_id=request.session_id,
                audience_epoch=request.audience_epoch,
                observation_id=request.observation_id,
                generation_request_id=request.generation_request_id,
                viewer_instance_id=request.viewer_instance_id,
            ),
        )
        try:
            self._ensure_available(self._viewer)
            content = await self._viewer_content(request)
            payload = self._json_payload(
                model_id=self.config.provider.viewer_model,
                system_prompt=_VIEWER_SYSTEM_PROMPT,
                content=content,
            )
            lifecycle.sent(build_openai_request_summary(payload))
            response = await self._send(self._viewer, payload, lifecycle=lifecycle)
            lifecycle.received(build_http_response_summary(response))
            output = self._structured_output(response)
            output.update(
                {
                    "generation_request_id": request.generation_request_id,
                    "viewer_instance_id": request.viewer_instance_id,
                    "viewer_sequence": request.viewer_sequence,
                }
            )
            self._canonicalize_evidence_refs(output)
            try:
                result = ViewerGenerationResponse.model_validate(output)
            except ValidationError as error:
                raise ViewerRuntimeProtocolError(
                    "Viewer response violated the ViewerGenerationResponse contract: "
                    f"{self._validation_codes(error)}"
                ) from None
            lifecycle.succeeded(result.model_dump(mode="json"))
            return result
        except asyncio.CancelledError:
            lifecycle.cancelled()
            raise
        except Exception as error:
            lifecycle.failed(error)
            raise

    async def summarize(
        self,
        wave: ObservationWave,
        frame_bundle: FrameBundle,
        runtime: object,
    ) -> str:
        lifecycle = self._call_lifecycle(
            role=AiCallRole.VISUAL_SUMMARY,
            correlation_id=wave.observation_id,
            model_id=self.config.provider.visual_summary_model,
            scope=AiCallScope(
                room_id=wave.room_id,
                session_id=wave.session_id,
                audience_epoch=wave.audience_epoch,
                observation_id=wave.observation_id,
            ),
        )
        try:
            self._ensure_available(self._visual_summary)
            if wave.frame_bundle != frame_bundle:
                raise ViewerRuntimeProtocolError(
                    "Visual summary FrameBundle did not match the wave"
                )
            context = {
                "wave": wave.model_dump(mode="json"),
                "runtime": self._json_value(runtime),
            }
            self._remove_data_refs(context)
            content = await self._content(context, wave.session_id, frame_bundle)
            if not isinstance(content, list) or len(content) < 2:
                raise ViewerRuntimeProviderBlockedError(
                    "Visual summary requires a resolvable FrameBundle"
                )
            payload = self._json_payload(
                model_id=self.config.provider.visual_summary_model,
                system_prompt=_VISUAL_SUMMARY_SYSTEM_PROMPT,
                content=content,
            )
            lifecycle.sent(build_openai_request_summary(payload))
            response = await self._send(
                self._visual_summary,
                payload,
                lifecycle=lifecycle,
            )
            lifecycle.received(build_http_response_summary(response))
            output = self._structured_output(response)
            summary = output.get("summary")
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 8_000:
                raise ViewerRuntimeProtocolError(
                    "Visual summary response violated the summary contract"
                )
            normalized_summary = summary.strip()
            lifecycle.succeeded({"summary": normalized_summary})
            return normalized_summary
        except asyncio.CancelledError:
            lifecycle.cancelled()
            raise
        except Exception as error:
            lifecycle.failed(error)
            raise

    async def summarize_history(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        existing_summary: str | None,
        older_history: str,
    ) -> str:
        if not older_history.strip():
            return (existing_summary or "").strip()
        context = json.dumps(
            {
                "session_id": session_id,
                "audience_epoch": audience_epoch,
                "existing_summary": existing_summary,
                "older_history": older_history,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload = self._json_payload(
            model_id=self.config.provider.viewer_model,
            system_prompt=_HISTORY_SUMMARY_SYSTEM_PROMPT,
            content=context,
        )
        response = await self._send(self._viewer, payload)
        output = self._structured_output(response)
        summary = output.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ViewerRuntimeProtocolError("History summary response was blank")
        return summary.strip()[:6_000]

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._viewer is not None:
                await self._viewer.aclose()
            if self._visual_summary is not None:
                await self._visual_summary.aclose()
            if self._owns_client:
                await self._client.aclose()

    def _role_provider(self, model_id: str) -> OpenAICompatibleProvider | None:
        if not self.config.api_key:
            return None
        return OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=self.config.base_url,
                model=model_id,
                api_key=self.config.api_key,
                request_timeout_seconds=self.config.request_timeout_seconds,
            ),
            client=self._client,
        )

    def _call_lifecycle(
        self,
        *,
        role: AiCallRole,
        correlation_id: str,
        model_id: str,
        scope: AiCallScope,
    ) -> AiCallLifecycle:
        return AiCallLifecycle(
            sink=self._ai_call_sink,
            role=role,
            correlation_id=correlation_id,
            provider="openai_compatible",
            model_id=model_id,
            endpoint=f"{self.config.base_url.rstrip('/')}/chat/completions",
            scope=scope,
        )

    async def _send(
        self,
        provider: OpenAICompatibleProvider | None,
        payload: dict[str, object],
        *,
        lifecycle: AiCallLifecycle,
    ) -> httpx.Response:
        self._ensure_available(provider)
        assert provider is not None
        try:
            return await provider._send(
                "POST",
                provider._chat_completions_endpoint(),
                payload=payload,
            )
        except asyncio.CancelledError:
            raise
        except OpenAICompatibleProviderError as error:
            if (
                isinstance(error, OpenAICompatibleHttpError)
                and error.response is not None
            ):
                lifecycle.received(build_http_response_summary(error.response))
            status_code = (
                error.status_code
                if isinstance(error, OpenAICompatibleHttpError)
                else None
            )
            retryable = (
                isinstance(
                    error,
                    (
                        OpenAICompatibleTimeoutError,
                        OpenAICompatibleTransportError,
                    ),
                )
                or status_code == 429
                or (
                    isinstance(status_code, int)
                    and 500 <= status_code <= 599
                )
            )
            raise ViewerRuntimeProviderError(
                str(error),
                status_code=status_code,
                retryable=retryable,
                retry_after_seconds=(
                    error.retry_after_seconds
                    if isinstance(error, OpenAICompatibleHttpError)
                    else None
                ),
            ) from error

    def _ensure_available(
        self,
        provider: OpenAICompatibleProvider | None,
    ) -> None:
        if self._closed:
            raise ViewerRuntimeProviderError("Viewer runtime provider is closed")
        if provider is None:
            raise ViewerRuntimeProviderBlockedError("Model provider credentials are not configured")

    async def _viewer_content(
        self,
        request: ViewerGenerationRequest,
    ) -> str | list[dict[str, object]]:
        context = request.model_dump(mode="json")
        bundle = request.frame_bundle
        self._remove_data_refs(context)
        content = await self._content(context, request.session_id, bundle)
        if (
            request.visual_input_mode is ViewerVisualInputMode.DIRECT_FRAMES
            and not isinstance(content, list)
        ):
            raise ViewerRuntimeProviderBlockedError(
                "direct_frames requires resolvable frames"
            )
        return content

    async def _content(
        self,
        context: dict[str, object],
        session_id: str,
        bundle: FrameBundle | None,
    ) -> str | list[dict[str, object]]:
        try:
            context_text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            raise ViewerRuntimeProtocolError("Role request was not JSON serializable") from None
        images = await self._image_parts(session_id, bundle)
        if not images:
            return context_text
        return [{"type": "text", "text": context_text}, *images]

    async def _image_parts(
        self,
        session_id: str,
        bundle: FrameBundle | None,
    ) -> list[dict[str, object]]:
        if self._frame_resolver is None or bundle is None:
            return []
        parts: list[dict[str, object]] = []
        for item in bundle.frames:
            mime_type = self._mime_type(item.encoding)
            try:
                resolved = await self._frame_resolver.resolve(
                    session_id=session_id,
                    frame=FrameRef(
                        frame_id=item.frame_id,
                        created_at_ms=item.captured_at_ms,
                        mime_type=mime_type,
                        data_ref=item.data_ref,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                raise ViewerRuntimeProtocolError("Frame resolution failed") from None
            if resolved is None:
                continue
            if resolved.session_id != session_id or resolved.frame_id != item.frame_id:
                raise ViewerRuntimeProtocolError("Frame resolver returned a mismatched frame")
            if resolved.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise ViewerRuntimeProtocolError(
                    "Frame resolver returned an unsupported image type"
                )
            encoded = base64.b64encode(resolved.body).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{resolved.mime_type};base64,{encoded}"},
                }
            )
        return parts

    def _json_payload(
        self,
        *,
        model_id: str,
        system_prompt: str,
        content: str | list[dict[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "stream": False,
            "n": 1,
            "max_tokens": _ROLE_OUTPUT_TOKEN_BUDGET,
        }
        payload.update(default_reasoning_options(self.config.base_url, model_id))
        return payload

    @staticmethod
    def _structured_output(response: httpx.Response) -> dict[str, object]:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise ViewerRuntimeProtocolError("Role response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise ViewerRuntimeProtocolError("Role response was not a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ViewerRuntimeProtocolError("Role response must contain exactly one choice")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ViewerRuntimeProtocolError("Role response choice did not contain a message")
        finish_reason = choice.get("finish_reason")
        if finish_reason not in {None, "stop"}:
            normalized_reason = (
                finish_reason
                if finish_reason in {"length", "content_filter", "tool_calls"}
                else "unexpected"
            )
            raise ViewerRuntimeProtocolError(
                f"Role response did not finish normally: {normalized_reason}",
                finish_reason=normalized_reason,
                token_budget=(
                    _ROLE_OUTPUT_TOKEN_BUDGET
                    if normalized_reason == "length"
                    else None
                ),
            )
        content = cast(dict[str, object], choice["message"]).get("content")
        if not isinstance(content, str):
            raise ViewerRuntimeProtocolError("Role response message did not contain JSON text")
        try:
            output = json.loads(content)
        except json.JSONDecodeError:
            raise ViewerRuntimeProtocolError("Role structured output was not valid JSON") from None
        if not isinstance(output, dict):
            raise ViewerRuntimeProtocolError("Role structured output was not a JSON object")
        return output

    @staticmethod
    def _validation_codes(error: ValidationError) -> str:
        codes = {
            f"{'.'.join(str(item) for item in detail['loc'])}:{detail['type']}"
            for detail in error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        }
        return ",".join(sorted(codes))[:512] or "unknown_validation_error"

    @staticmethod
    def _canonicalize_evidence_refs(output: dict[str, object]) -> None:
        references = output.get("evidence_refs")
        if not isinstance(references, list):
            return
        for reference in references:
            if not isinstance(reference, dict):
                continue
            if reference.get("source") == "event":
                reference["frame_index"] = None
            elif reference.get("source") == "frame":
                reference["event_id"] = None

    @staticmethod
    def _json_value(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if is_dataclass(value) and not isinstance(value, type):
            return {
                item.name: OpenAICompatibleViewerRuntimeProvider._json_value(
                    getattr(value, item.name)
                )
                for item in dataclass_fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): OpenAICompatibleViewerRuntimeProvider._json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                OpenAICompatibleViewerRuntimeProvider._json_value(item)
                for item in value
            ]
        if isinstance(value, Enum):
            return value.value
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ViewerRuntimeProtocolError("Viewer runtime context is not serializable")

    @classmethod
    def _remove_data_refs(cls, value: object) -> None:
        if isinstance(value, dict):
            value.pop("data_ref", None)
            for child in value.values():
                cls._remove_data_refs(child)
        elif isinstance(value, list):
            for child in value:
                cls._remove_data_refs(child)

    @staticmethod
    def _mime_type(encoding: str) -> str:
        normalized = encoding.lower()
        if normalized.startswith("image/"):
            return normalized
        if normalized == "jpg":
            normalized = "jpeg"
        return f"image/{normalized}"


__all__ = [
    "OpenAICompatibleViewerRuntimeConfig",
    "OpenAICompatibleViewerRuntimeProvider",
    "VisualSummaryProvider",
    "ViewerRuntimeProtocolError",
    "ViewerRuntimeProviderBlockedError",
    "ViewerRuntimeProviderError",
]
