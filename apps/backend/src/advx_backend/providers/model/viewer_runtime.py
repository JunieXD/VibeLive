import asyncio
import base64
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import Final, Protocol, cast

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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
    MAX_VIEWER_BARRAGE_BATCH_SIZE,
    EvidenceRef,
    ProviderRuntimeSpec,
    ViewerAction,
    ViewerBarrageText,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
    ViewerReactionIntent,
    ViewerReactionTarget,
    WindowBatchGenerationRequest,
    WindowBatchGenerationResponse,
    normalize_viewer_barrage_texts,
)
from advx_backend.domain.observation import FrameRef
from advx_backend.domain.observation_wave import (
    FrameBundle,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.providers.model.openai_compatible import (
    JSON_MODE_RESPONSE_FORMAT,
    OpenAICompatibleConfig,
    OpenAICompatibleHttpError,
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
    OpenAICompatibleTimeoutError,
    OpenAICompatibleTransportError,
    default_reasoning_options,
)
from advx_backend.providers.model.provider_rate_gate import ProviderRateGate
from advx_backend.providers.model.style_guidance import style_guidance_for


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
    '{"action":"barrage","intent":"react_to_host",'
    '"target":null,"texts":["这波漂亮"],"reaction_type":"comment",'
    '"decision_reason":"主播的提问符合当前人设",'
    '"evidence_refs":[{"source":"event","event_id":"event-id"}]}'
)
_VIEWER_SILENCE_JSON_EXAMPLE: Final = (
    '{"action":"silence","intent":"silence",'
    '"target":null,"texts":null,"reaction_type":"silence",'
    '"decision_reason":"普通问候未触发当前人设","evidence_refs":[]}'
)
_SUMMARY_JSON_EXAMPLE: Final = '{"summary":"画面中的关键变化"}'
_WINDOW_BATCH_JSON_EXAMPLE: Final = (
    '{"candidates":[{"viewer_instance_id":"allowed-viewer-id",'
    '"action":"barrage","intent":"react_to_scene","target":null,'
    '"texts":["这波看懂了"],"reaction_type":"comment",'
    '"decision_reason":"画面出现关键变化",'
    '"evidence_refs":[{"source":"frame","frame_index":0}]}]}'
)
_VIEWER_SYSTEM_PROMPT: Final = (
    "Act as exactly the supplied viewer instance. The username is your identity; the Persona "
    "is only a behavioral tendency and is not your name or a system role. Produce silence or a "
    "short burst of one to three natural barrage reactions. You may react to the host, scene, "
    "or a replyable public Viewer "
    "event, but may target only IDs explicitly allowed by the request. Shared room memory is "
    "public background, not proof that you personally attended an earlier stream. Use only "
    "evidence references present in the input. When mode_context.style_profile is supplied, "
    "treat its aggregate length, cadence, directives, and persona lens as binding style "
    "guidance, but never treat it as scene evidence or reconstruct source corpus text. "
    "evidence_refs must be a JSON array of objects, "
    "never bare IDs or numbers. Use [] when no citation is needed. An event reference is "
    '{"source":"event","event_id":"allowed-event-id"}; a frame reference is '
    '{"source":"frame","frame_index":0}. Use only allowed event IDs and zero-based frame '
    "indexes from the input. Include decision_reason for every result: one concise Chinese "
    "sentence of 40 characters or fewer stating the visible persona or evidence basis for the "
    "barrage or silence decision. Do not include hidden reasoning, probabilities, or chain of "
    "thought. "
    "Legal intent values are exactly: react_to_host, react_to_scene, reply_to_viewer, "
    "ask_question, agree, disagree, encourage, joke, continue_thread, room_meta, silence. "
    "Legal target.kind values are exactly: host, scene, room, viewer, event. target must be "
    "null or an object; never return a string. Every target object must include kind. "
    "For a host, scene, or room target, viewer_instance_id and event_id must both be null. "
    "For a viewer target, provide viewer_instance_id only; for an event target, provide event_id "
    "only. No other target fields are allowed. Use null, never an empty string, for every absent "
    "target ID. For action=barrage, texts must be a JSON array containing one to three distinct, "
    "non-empty strings. Each entry must be a complete standalone barrage. Do not split one "
    "sentence or repeat the same point across entries. For action=silence, intent must be silence, "
    "target and texts must be null, and reaction_type must be silence. Do not return "
    "generation_request_id, "
    "viewer_instance_id, or viewer_sequence; "
    "the server owns those fields. Unless a supplied style profile overrides this default, "
    "prefer a natural Chinese message of 20 characters or fewer. Return "
    "exactly one JSON object, with no Markdown or prose. "
    f"For a barrage use this shape: {_VIEWER_BARRAGE_JSON_EXAMPLE} "
    f"For no response use this shape: {_VIEWER_SILENCE_JSON_EXAMPLE}"
)
_WINDOW_BATCH_SYSTEM_PROMPT: Final = (
    "Act as the locally selected viewer instances supplied in the request. Produce zero or "
    "more natural barrage candidates in one response. Each candidate must use exactly one "
    "viewer_instance_id from viewers and no viewer may appear twice. Omit viewers who should "
    "stay silent. A viewer username is its identity; Persona is only a behavioral tendency. "
    "Shared room memory is public background, not proof that a viewer attended an earlier "
    "stream. Treat each style_profile as binding style guidance, but never as scene evidence "
    "and never reconstruct its source corpus text. Candidate action must be barrage and texts "
    "must be a JSON array containing one to three distinct complete barrage messages. Do not "
    "split one sentence or repeat the same point across entries. Each message should be 20 "
    "Chinese characters or fewer unless its style_profile requires otherwise. Legal intent values "
    "are exactly: react_to_host, react_to_scene, reply_to_viewer, ask_question, agree, disagree, "
    "encourage, joke, continue_thread, room_meta. target must be null or an object with kind "
    "host, scene, room, viewer, or event. Host, scene, and room targets must set both "
    "viewer_instance_id and event_id to null. A viewer target must set viewer_instance_id to "
    "an active_viewer_ids value and event_id to null. An event target must set event_id to a "
    "scene_assessment.replyable_event_ids value and viewer_instance_id to null. No other target "
    "fields are allowed. evidence_refs must be an array "
    'of {"source":"event","event_id":"allowed-event-id"} or '
    '{"source":"frame","frame_index":0} objects using only IDs and zero-based frame indexes in '
    "the request. Include one concise Chinese decision_reason of 40 characters or fewer. Do not "
    "return generation_request_id or viewer_sequence; the server owns them. Do not expose hidden "
    "reasoning. Return exactly one JSON object with no Markdown or prose. Use this shape: "
    f"{_WINDOW_BATCH_JSON_EXAMPLE}"
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
_REPAIR_TIMEOUT_SECONDS: Final = 6.0
_MIN_REPAIR_REMAINING_SECONDS: Final = 6.0
_CALL_STATE_CAPACITY: Final = 4_096


class _ViewerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ViewerAction
    intent: ViewerReactionIntent = ViewerReactionIntent.REACT_TO_SCENE
    target: ViewerReactionTarget | None = None
    texts: list[ViewerBarrageText] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_VIEWER_BARRAGE_BATCH_SIZE,
    )
    reaction_type: str = Field(min_length=1, max_length=64)
    decision_reason: str | None = Field(default=None, min_length=1, max_length=160)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=128)

    @field_validator("texts")
    @classmethod
    def normalize_texts(cls, value: list[str] | None) -> list[str] | None:
        return normalize_viewer_barrage_texts(value)

    @model_validator(mode="after")
    def validate_action(self) -> "_ViewerModelOutput":
        if self.action is ViewerAction.BARRAGE and self.texts is None:
            raise ValueError("barrage requires texts")
        if self.action is ViewerAction.SILENCE:
            if self.intent is not ViewerReactionIntent.SILENCE:
                raise ValueError("silence action requires silence intent")
            if self.target is not None or self.texts is not None:
                raise ValueError("silence cannot include target or texts")
            if self.reaction_type != "silence":
                raise ValueError("silence action requires silence reaction_type")
        return self


class _WindowBatchCandidate(_ViewerModelOutput):
    viewer_instance_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_barrage_only(self) -> "_WindowBatchCandidate":
        if self.action is not ViewerAction.BARRAGE:
            raise ValueError("window batch candidates must be barrages")
        return self


class _WindowBatchModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[_WindowBatchCandidate] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_unique_viewers(self) -> "_WindowBatchModelOutput":
        viewer_ids = [candidate.viewer_instance_id for candidate in self.candidates]
        if len(viewer_ids) != len(set(viewer_ids)):
            raise ValueError("window batch candidate viewer IDs must be unique")
        return self


@dataclass(slots=True)
class _ViewerCallState:
    deadline_at_ms: int
    invocations: int = 0
    provider_calls: int = 0


class OpenAICompatibleViewerRuntimeProvider:
    """Per-viewer model adapter for one active provider profile."""

    def __init__(
        self,
        config: OpenAICompatibleViewerRuntimeConfig,
        *,
        client: httpx.AsyncClient | None = None,
        frame_resolver: FrameResolver | None = None,
        ai_call_sink: AiCallSink | None = None,
        rate_gate: ProviderRateGate | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.config = config
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None
        self._frame_resolver = frame_resolver
        self._ai_call_sink = ai_call_sink
        self._rate_gate = rate_gate or ProviderRateGate()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._viewer = self._role_provider(config.provider.viewer_model)
        self._visual_summary = self._role_provider(config.provider.visual_summary_model)
        self._viewer_call_states: OrderedDict[str, _ViewerCallState] = OrderedDict()
        self._viewer_call_state_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def generate(self, request: ViewerGenerationRequest) -> ViewerGenerationResponse:
        invocation = await self._begin_viewer_invocation(request)
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
            await self._reserve_viewer_call(request)
            response = await self._send_rate_limited(
                self._viewer,
                payload,
                lifecycle=lifecycle,
                viewer_request=request,
                allow_json_mode_fallback=False,
            )
            lifecycle.received(build_http_response_summary(response))
            output = self._structured_output(response)
            try:
                model_output = self._validate_viewer_output(output)
            except ValidationError as error:
                if invocation == 1 and self._can_repair(request):
                    await self._reserve_viewer_call(request)
                    repair_payload = self._repair_payload(
                        payload,
                        validation_codes=self._validation_codes(error),
                    )
                    response = await self._send_rate_limited(
                        self._viewer,
                        repair_payload,
                        lifecycle=lifecycle,
                        viewer_request=request,
                        maximum_timeout_seconds=_REPAIR_TIMEOUT_SECONDS,
                        allow_json_mode_fallback=False,
                    )
                    lifecycle.received(build_http_response_summary(response))
                    output = self._structured_output(response)
                    try:
                        model_output = self._validate_viewer_output(output)
                    except ValidationError as repair_error:
                        raise self._viewer_validation_error(repair_error) from None
                else:
                    raise self._viewer_validation_error(error) from None
            result = ViewerGenerationResponse.model_validate(
                {
                    **model_output.model_dump(mode="json"),
                    "generation_request_id": request.generation_request_id,
                    "viewer_instance_id": request.viewer_instance_id,
                    "viewer_sequence": request.viewer_sequence,
                }
            )
            lifecycle.succeeded(result.model_dump(mode="json"))
            return result
        except asyncio.CancelledError:
            lifecycle.cancelled()
            raise
        except Exception as error:
            lifecycle.failed(error)
            raise

    async def generate_window_batch(
        self,
        request: WindowBatchGenerationRequest,
    ) -> WindowBatchGenerationResponse:
        lifecycle = self._call_lifecycle(
            role=AiCallRole.VIEWER,
            correlation_id=request.batch_generation_request_id,
            model_id=self.config.provider.viewer_model,
            scope=AiCallScope(
                room_id=request.room_id,
                session_id=request.session_id,
                audience_epoch=request.audience_epoch,
                observation_id=request.observation_id,
                generation_request_id=request.batch_generation_request_id,
            ),
        )
        try:
            self._ensure_available(self._viewer)
            content = await self._window_batch_content(request)
            payload = self._json_payload(
                model_id=self.config.provider.viewer_model,
                system_prompt=_WINDOW_BATCH_SYSTEM_PROMPT,
                content=content,
            )
            response = await self._send_rate_limited(
                self._viewer,
                payload,
                lifecycle=lifecycle,
                viewer_request=request.requests[0],
                allow_json_mode_fallback=False,
            )
            lifecycle.received(build_http_response_summary(response))
            output = self._structured_output(response)
            try:
                model_output = _WindowBatchModelOutput.model_validate(output)
            except ValidationError as error:
                raise ViewerRuntimeProtocolError(
                    "Window batch response violated the model output contract: "
                    f"{self._validation_codes(error)}"
                ) from None
            request_by_viewer = {
                item.viewer_instance_id: item for item in request.requests
            }
            candidates: list[ViewerGenerationResponse] = []
            for candidate in model_output.candidates:
                viewer_request = request_by_viewer.get(candidate.viewer_instance_id)
                if viewer_request is None:
                    raise ViewerRuntimeProtocolError(
                        "Window batch response used an unselected viewer ID"
                    )
                candidates.append(
                    ViewerGenerationResponse.model_validate(
                        {
                            **candidate.model_dump(
                                mode="json",
                                exclude={"viewer_instance_id"},
                            ),
                            "generation_request_id": viewer_request.generation_request_id,
                            "viewer_instance_id": viewer_request.viewer_instance_id,
                            "viewer_sequence": viewer_request.viewer_sequence,
                        }
                    )
                )
            result = WindowBatchGenerationResponse(
                batch_generation_request_id=request.batch_generation_request_id,
                candidates=candidates,
            )
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
            response = await self._send_rate_limited(
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
        lifecycle = self._call_lifecycle(
            role=AiCallRole.HISTORY_SUMMARY,
            correlation_id=(
                "history-"
                f"{hashlib.sha256(context.encode('utf-8')).hexdigest()[:32]}"
            ),
            model_id=self.config.provider.viewer_model,
            scope=AiCallScope(
                session_id=session_id,
                audience_epoch=audience_epoch,
            ),
        )
        try:
            self._ensure_available(self._viewer)
            payload = self._json_payload(
                model_id=self.config.provider.viewer_model,
                system_prompt=_HISTORY_SUMMARY_SYSTEM_PROMPT,
                content=context,
            )
            response = await self._send_rate_limited(
                self._viewer,
                payload,
                lifecycle=lifecycle,
            )
            lifecycle.received(build_http_response_summary(response))
            output = self._structured_output(response)
            summary = output.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise ViewerRuntimeProtocolError("History summary response was blank")
            normalized_summary = summary.strip()[:6_000]
            lifecycle.succeeded({"summary": normalized_summary})
            return normalized_summary
        except asyncio.CancelledError:
            lifecycle.cancelled()
            raise
        except Exception as error:
            lifecycle.failed(error)
            raise

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

    async def _send_rate_limited(
        self,
        provider: OpenAICompatibleProvider | None,
        payload: dict[str, object],
        *,
        lifecycle: AiCallLifecycle,
        capture_model_output: bool = False,
        request_timeout_seconds: float | None = None,
        allow_json_mode_fallback: bool = True,
        viewer_request: ViewerGenerationRequest | None = None,
        maximum_timeout_seconds: float | None = None,
    ) -> httpx.Response:
        async with self._rate_gate.lease() as rate_limit_generation:
            if viewer_request is not None:
                request_timeout_seconds = self._effective_timeout(
                    viewer_request,
                    maximum_seconds=maximum_timeout_seconds,
                )
            lifecycle.sent(build_openai_request_summary(payload, image_capture=lifecycle))
            try:
                response = await self._send(
                    provider,
                    payload,
                    lifecycle=lifecycle,
                    capture_model_output=capture_model_output,
                    request_timeout_seconds=request_timeout_seconds,
                    allow_json_mode_fallback=allow_json_mode_fallback,
                )
            except ViewerRuntimeProviderError as error:
                if error.status_code == 429:
                    await self._rate_gate.defer_for_rate_limit(
                        error.retry_after_seconds
                    )
                raise
            else:
                await self._rate_gate.record_success(rate_limit_generation)
        return response

    async def _send(
        self,
        provider: OpenAICompatibleProvider | None,
        payload: dict[str, object],
        *,
        lifecycle: AiCallLifecycle,
        capture_model_output: bool = False,
        request_timeout_seconds: float | None = None,
        allow_json_mode_fallback: bool = True,
    ) -> httpx.Response:
        self._ensure_available(provider)
        assert provider is not None
        try:
            return await provider._send(
                "POST",
                provider._chat_completions_endpoint(),
                payload=payload,
                request_timeout_seconds=request_timeout_seconds,
                allow_json_mode_fallback=allow_json_mode_fallback,
            )
        except asyncio.CancelledError:
            raise
        except OpenAICompatibleProviderError as error:
            if (
                isinstance(error, OpenAICompatibleHttpError)
                and error.response is not None
            ):
                lifecycle.received(
                    build_http_response_summary(
                        error.response,
                        include_model_output=capture_model_output,
                    )
                )
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
        mode_context = context.get("mode_context")
        if not isinstance(mode_context, dict):
            raise ViewerRuntimeProtocolError("Viewer mode context was not an object")
        mode_context.pop("style_profile", None)
        style_guidance = style_guidance_for(
            request.mode_context,
            persona_id=request.persona.persona_id,
        )
        if style_guidance is not None:
            mode_context["style_profile"] = style_guidance
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

    async def _window_batch_content(
        self,
        request: WindowBatchGenerationRequest,
    ) -> str | list[dict[str, object]]:
        first = request.requests[0]
        mode_context = dict(first.mode_context)
        mode_context.pop("style_profile", None)
        mode_context.pop("_viewer_persona_id", None)
        mode_context.pop("_viewer_display_name", None)
        viewers: list[dict[str, object]] = []
        for viewer_request in request.requests:
            style_guidance = style_guidance_for(
                viewer_request.mode_context,
                persona_id=viewer_request.persona.persona_id,
            )
            viewer_context: dict[str, object] = {
                "viewer_instance_id": viewer_request.viewer_instance_id,
                "username": viewer_request.username,
                "display_name": viewer_request.display_name,
                "persona": viewer_request.persona.model_dump(mode="json"),
                "instance_variant": viewer_request.instance_variant.model_dump(mode="json"),
                "viewer_private_state": viewer_request.viewer_private_state.model_dump(mode="json"),
            }
            if style_guidance is not None:
                viewer_context["style_profile"] = style_guidance
            viewers.append(viewer_context)
        context: dict[str, object] = {
            "batch_generation_request_id": request.batch_generation_request_id,
            "room_id": request.room_id,
            "session_id": request.session_id,
            "audience_epoch": request.audience_epoch,
            "observation_id": request.observation_id,
            "deadline_at_ms": request.deadline_at_ms,
            "scene_assessment": first.scene_assessment.model_dump(mode="json"),
            "active_viewer_ids": first.active_viewer_ids,
            "mode_context": mode_context,
            "visual_input_mode": first.visual_input_mode.value,
            "frame_bundle": (
                None
                if first.frame_bundle is None
                else first.frame_bundle.model_dump(mode="json")
            ),
            "shared_visual_summary": first.shared_visual_summary,
            "input_event_ids": first.input_event_ids,
            "public_context_event_ids": first.public_context_event_ids,
            "public_context": [
                event.model_dump(mode="json") for event in first.public_context
            ],
            "reply_context_event_ids": first.reply_context_event_ids,
            "reply_context": [
                event.model_dump(mode="json") for event in first.reply_context
            ],
            "conversation_history_summary": first.conversation_history_summary,
            "room_memory_slice": first.room_memory_slice.model_dump(mode="json"),
            "viewers": viewers,
        }
        self._remove_data_refs(context)
        content = await self._content(context, request.session_id, first.frame_bundle)
        if (
            first.visual_input_mode is ViewerVisualInputMode.DIRECT_FRAMES
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
            "response_format": JSON_MODE_RESPONSE_FORMAT,
        }
        payload.update(default_reasoning_options(self.config.base_url, model_id))
        return payload

    async def _begin_viewer_invocation(self, request: ViewerGenerationRequest) -> int:
        async with self._viewer_call_state_lock:
            now_ms = self._clock_ms()
            expired = [
                request_id
                for request_id, state in self._viewer_call_states.items()
                if state.deadline_at_ms <= now_ms
            ]
            for request_id in expired:
                self._viewer_call_states.pop(request_id, None)
            state = self._viewer_call_states.get(request.generation_request_id)
            if state is None:
                if len(self._viewer_call_states) >= _CALL_STATE_CAPACITY:
                    raise ViewerRuntimeProtocolError(
                        "Viewer provider call-state capacity was exhausted"
                    )
                state = _ViewerCallState(deadline_at_ms=request.deadline_at_ms)
                self._viewer_call_states[request.generation_request_id] = state
            else:
                state.deadline_at_ms = max(state.deadline_at_ms, request.deadline_at_ms)
                self._viewer_call_states.move_to_end(request.generation_request_id)
            state.invocations += 1
            return state.invocations

    async def _reserve_viewer_call(self, request: ViewerGenerationRequest) -> None:
        async with self._viewer_call_state_lock:
            state = self._viewer_call_states.get(request.generation_request_id)
            if state is None or state.provider_calls >= 2:
                raise ViewerRuntimeProtocolError(
                    "Viewer provider call budget was exhausted"
                )
            state.provider_calls += 1

    def _remaining_seconds(self, request: ViewerGenerationRequest) -> float:
        return max(0.0, (request.deadline_at_ms - self._clock_ms()) / 1_000)

    def _effective_timeout(
        self,
        request: ViewerGenerationRequest,
        *,
        maximum_seconds: float | None,
    ) -> float:
        remaining_seconds = self._remaining_seconds(request)
        if remaining_seconds <= 0:
            raise ViewerRuntimeProtocolError("Viewer request deadline expired")
        timeout_seconds = min(
            float(self.config.request_timeout_seconds),
            remaining_seconds,
        )
        if maximum_seconds is not None:
            timeout_seconds = min(timeout_seconds, maximum_seconds)
        return timeout_seconds

    def _can_repair(self, request: ViewerGenerationRequest) -> bool:
        return self._remaining_seconds(request) >= _MIN_REPAIR_REMAINING_SECONDS

    @staticmethod
    def _repair_payload(
        payload: dict[str, object],
        *,
        validation_codes: str,
    ) -> dict[str, object]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ViewerRuntimeProtocolError("Viewer repair payload had no messages")
        return {
            **payload,
            "messages": [
                *messages,
                {
                    "role": "system",
                    "content": (
                        "Your prior JSON violated the contract. Return one corrected JSON "
                        f"object only. Validation codes: {validation_codes}"
                    ),
                },
            ],
        }

    @classmethod
    def _validate_viewer_output(
        cls,
        output: dict[str, object],
    ) -> _ViewerModelOutput:
        normalized = dict(output)
        for server_owned in (
            "generation_request_id",
            "viewer_instance_id",
            "viewer_sequence",
        ):
            normalized.pop(server_owned, None)
        cls._canonicalize_target(normalized)
        cls._canonicalize_evidence_refs(normalized)
        return _ViewerModelOutput.model_validate(normalized)

    @classmethod
    def _viewer_validation_error(
        cls,
        error: ValidationError,
    ) -> ViewerRuntimeProtocolError:
        return ViewerRuntimeProtocolError(
            "Viewer response violated the model output contract: "
            f"{cls._validation_codes(error)}"
        )

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
    def _canonicalize_target(output: dict[str, object]) -> None:
        target = output.get("target")
        if not isinstance(target, dict):
            return
        for identifier in ("viewer_instance_id", "event_id"):
            if target.get(identifier) == "":
                target[identifier] = None

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
