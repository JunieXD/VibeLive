import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import Final, Protocol, cast

import httpx
from pydantic import BaseModel, ValidationError

from advx_backend.application.director_service import DirectorOutcome, DirectorRequest
from advx_backend.application.ports.ingest import FrameResolver
from advx_backend.contracts.viewer_runtime import (
    ProviderRuntimeSpec,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
)
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.observation import FrameRef
from advx_backend.domain.observation_wave import (
    FrameBundle,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleHttpError,
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
    OpenAICompatibleTimeoutError,
    OpenAICompatibleTransportError,
)


class ViewerRuntimeProviderError(RuntimeError):
    """Normalized failure from the role-aware runtime model adapter."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
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


_DIRECTOR_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "assessment_id",
        "salience",
        "novelty",
        "emotional_intensity",
        "topics",
        "emotional_tone",
        "replyable_event_ids",
        "reason_codes",
        "evidence_event_ids",
        "evidence_frame_indexes",
        "suggested_reaction_types",
        "maximum_responses",
        "meme_candidate",
    ],
    "properties": {
        "assessment_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "salience": {"type": "number", "minimum": 0, "maximum": 1},
        "novelty": {"type": "number", "minimum": 0, "maximum": 1},
        "emotional_intensity": {"type": "number", "minimum": 0, "maximum": 1},
        "topics": {
            "type": "array",
            "maxItems": 32,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "emotional_tone": {
            "type": "array",
            "maxItems": 16,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "replyable_event_ids": {
            "type": "array",
            "maxItems": 128,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "reason_codes": {
            "type": "array",
            "maxItems": 32,
            "items": {"type": "string", "minLength": 1},
        },
        "evidence_event_ids": {
            "type": "array",
            "maxItems": 128,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "evidence_frame_indexes": {
            "type": "array",
            "maxItems": 32,
            "items": {"type": "integer", "minimum": 0},
        },
        "suggested_reaction_types": {
            "type": "array",
            "maxItems": 32,
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "maximum_responses": {"type": "integer", "minimum": 0, "maximum": 32},
        "meme_candidate": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "text",
                        "evidence_event_ids",
                        "evidence_frame_indexes",
                    ],
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 500},
                        "evidence_event_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 128,
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                            },
                        },
                        "evidence_frame_indexes": {
                            "type": "array",
                            "maxItems": 32,
                            "items": {"type": "integer", "minimum": 0},
                        },
                    },
                },
            ]
        },
    },
}


def _director_schema(frame_count: int) -> dict[str, object]:
    schema = deepcopy(_DIRECTOR_SCHEMA)
    properties = cast(dict[str, object], schema["properties"])
    meme_candidate = cast(dict[str, object], properties["meme_candidate"])
    meme_alternatives = cast(list[object], meme_candidate["anyOf"])
    meme_schema = cast(dict[str, object], meme_alternatives[1])
    meme_properties = cast(dict[str, object], meme_schema["properties"])
    frame_index_schemas = [
        cast(dict[str, object], properties["evidence_frame_indexes"]),
        cast(dict[str, object], meme_properties["evidence_frame_indexes"]),
    ]
    for frame_index_schema in frame_index_schemas:
        if frame_count == 0:
            frame_index_schema["maxItems"] = 0
            continue
        items = cast(dict[str, object], frame_index_schema["items"])
        items["maximum"] = frame_count - 1
    return schema


_EVIDENCE_REF_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "event_id", "frame_index"],
    "properties": {
        "source": {"type": "string", "enum": ["event", "frame"]},
        "event_id": {
            "anyOf": [
                {"type": "string", "minLength": 1, "maxLength": 128},
                {"type": "null"},
            ]
        },
        "frame_index": {
            "anyOf": [
                {"type": "integer", "minimum": 0},
                {"type": "null"},
            ]
        },
    },
}
_VIEWER_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "generation_request_id",
        "viewer_instance_id",
        "viewer_sequence",
        "action",
        "intent",
        "target",
        "text",
        "reaction_type",
        "evidence_refs",
    ],
    "properties": {
        "generation_request_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "viewer_instance_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "viewer_sequence": {"type": "integer", "minimum": 1},
        "action": {"type": "string", "enum": ["barrage", "silence"]},
        "intent": {
            "type": "string",
            "enum": [
                "react_to_host",
                "react_to_scene",
                "reply_to_viewer",
                "ask_question",
                "agree",
                "disagree",
                "encourage",
                "joke",
                "continue_thread",
                "room_meta",
                "silence",
            ],
        },
        "target": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "viewer_instance_id", "event_id"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["host", "scene", "room", "viewer", "event"],
                        },
                        "viewer_instance_id": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                        "event_id": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                    },
                },
                {"type": "null"},
            ]
        },
        "text": {
            "anyOf": [
                {"type": "string", "minLength": 1, "maxLength": 200},
                {"type": "null"},
            ]
        },
        "reaction_type": {"type": "string", "minLength": 1, "maxLength": 64},
        "evidence_refs": {
            "type": "array",
            "maxItems": 128,
            "items": _EVIDENCE_REF_SCHEMA,
        },
    },
}
_VISUAL_SUMMARY_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 8_000},
    },
}
_DIRECTOR_SYSTEM_PROMPT: Final = (
    "Assess the supplied live scene without selecting any viewer or writing barrage text. "
    "Use only event IDs and evidence references present in the input, and never exceed maximum. "
    "If the same non-empty phrase appears at least three times "
    "inside a visible public event, you MUST emit meme_candidate, copy the shortest "
    "repeated phrase verbatim as its text without paraphrasing, and cite that event. "
    "Otherwise set meme_candidate to null. "
    "Return only the required JSON object."
)
_VIEWER_SYSTEM_PROMPT: Final = (
    "Act as exactly the supplied viewer instance. The username is your identity; the Persona "
    "is only a behavioral tendency and is not your name or a system role. Produce zero or one "
    "natural barrage reaction. You may react to the host, scene, or a replyable public Viewer "
    "event, but may target only IDs explicitly allowed by the request. Shared room memory is "
    "public background, not proof that you personally attended an earlier stream. Use only "
    "evidence references present in the input. Return only the required JSON object."
)
_VISUAL_SUMMARY_SYSTEM_PROMPT: Final = (
    "Summarize only visible, decision-relevant changes across the ordered frame bundle. "
    "Do not invent events or identities. Return only the required JSON object."
)
_ROLE_OUTPUT_TOKEN_BUDGET: Final = 4_096


class OpenAICompatibleViewerRuntimeProvider:
    """Role-aware Director and per-Viewer adapter for one active provider profile."""

    def __init__(
        self,
        config: OpenAICompatibleViewerRuntimeConfig,
        *,
        client: httpx.AsyncClient | None = None,
        frame_resolver: FrameResolver | None = None,
    ) -> None:
        self.config = config
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None
        self._frame_resolver = frame_resolver
        self._director = self._role_provider(config.provider.director_model)
        self._viewer = self._role_provider(config.provider.viewer_model)
        self._visual_summary = self._role_provider(config.provider.visual_summary_model)
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def decide(self, request: object) -> DirectorOutcome:
        if not isinstance(request, DirectorRequest):
            raise ViewerRuntimeProtocolError("Director request has an invalid contract")
        content = await self._director_content(request)
        frame_count = (
            0 if request.wave.frame_bundle is None else len(request.wave.frame_bundle.frames)
        )
        payload = self._structured_payload(
            model_id=self.config.provider.director_model,
            system_prompt=_DIRECTOR_SYSTEM_PROMPT,
            content=content,
            schema_name="scene_assessment",
            schema=_director_schema(frame_count),
        )
        response = await self._send(self._director, payload)
        output = self._structured_output(response)
        raw_meme_candidate = output.pop("meme_candidate", None)
        output.update(
            {
                "room_id": request.wave.room_id,
                "session_id": request.wave.session_id,
                "audience_epoch": request.wave.audience_epoch,
                "observation_id": request.wave.observation_id,
                "decision_source": "director",
                "created_at_ms": request.wave.created_at_ms,
                "expires_at_ms": request.wave.deadline_at_ms,
            }
        )
        try:
            assessment = SceneAssessment.model_validate(output)
        except ValidationError as error:
            raise ViewerRuntimeProtocolError(
                "Director response violated the SceneAssessment contract: "
                f"{self._validation_codes(error)}"
            ) from None
        candidate = self._meme_candidate(request, raw_meme_candidate)
        return DirectorOutcome(assessment=assessment, meme_candidate=candidate)

    async def generate(self, request: ViewerGenerationRequest) -> ViewerGenerationResponse:
        content = await self._viewer_content(request)
        payload = self._structured_payload(
            model_id=self.config.provider.viewer_model,
            system_prompt=_VIEWER_SYSTEM_PROMPT,
            content=content,
            schema_name="viewer_generation_response",
            schema=_VIEWER_SCHEMA,
        )
        response = await self._send(self._viewer, payload)
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
        return result

    async def summarize(
        self,
        wave: ObservationWave,
        frame_bundle: FrameBundle,
        runtime: object,
    ) -> str:
        if self._closed:
            raise ViewerRuntimeProviderError("Viewer runtime provider is closed")
        if self._visual_summary is None:
            raise ViewerRuntimeProviderBlockedError("Model provider credentials are not configured")
        if wave.frame_bundle != frame_bundle:
            raise ViewerRuntimeProtocolError("Visual summary FrameBundle did not match the wave")
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
        payload = self._structured_payload(
            model_id=self.config.provider.visual_summary_model,
            system_prompt=_VISUAL_SUMMARY_SYSTEM_PROMPT,
            content=content,
            schema_name="visual_summary",
            schema=_VISUAL_SUMMARY_SCHEMA,
        )
        response = await self._send(self._visual_summary, payload)
        output = self._structured_output(response)
        summary = output.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 8_000:
            raise ViewerRuntimeProtocolError(
                "Visual summary response violated the summary contract"
            )
        return summary.strip()

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._director is not None:
                await self._director.aclose()
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

    async def _send(
        self,
        provider: OpenAICompatibleProvider | None,
        payload: dict[str, object],
    ) -> httpx.Response:
        if self._closed:
            raise ViewerRuntimeProviderError("Viewer runtime provider is closed")
        if provider is None:
            raise ViewerRuntimeProviderBlockedError("Model provider credentials are not configured")
        try:
            return await provider._send(
                "POST",
                provider._chat_completions_endpoint(),
                payload=payload,
            )
        except asyncio.CancelledError:
            raise
        except OpenAICompatibleProviderError as error:
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
            ) from error

    async def _director_content(
        self,
        request: DirectorRequest,
    ) -> str | list[dict[str, object]]:
        context = {
            "wave": request.wave.model_dump(mode="json"),
            "maximum": request.maximum,
            "runtime": self._json_value(request.runtime),
        }
        bundle = request.wave.frame_bundle
        self._remove_data_refs(context)
        return await self._content(context, request.wave.session_id, bundle)

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

    @staticmethod
    def _structured_payload(
        *,
        model_id: str,
        system_prompt: str,
        content: str | list[dict[str, object]],
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        return {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "stream": False,
            "n": 1,
            "max_tokens": _ROLE_OUTPUT_TOKEN_BUDGET,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }

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
    def _meme_candidate(
        request: DirectorRequest,
        raw: object,
    ) -> MemeCandidate | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ViewerRuntimeProtocolError("Director meme candidate was not an object")
        text = raw.get("text")
        event_ids = raw.get("evidence_event_ids")
        frame_indexes = raw.get("evidence_frame_indexes")
        if (
            not isinstance(text, str)
            or not isinstance(event_ids, list)
            or not all(isinstance(item, str) for item in event_ids)
            or not isinstance(frame_indexes, list)
            or not all(isinstance(item, int) for item in frame_indexes)
        ):
            raise ViewerRuntimeProtocolError(
                "Director meme candidate violated its compact contract"
            )
        if not event_ids:
            normalized_text = text.strip().casefold()
            event_ids = [
                event.event_id
                for event in getattr(request.runtime, "public_context", ())
                if (
                    event.event_id in request.wave.event_ids
                    and isinstance(event.text, str)
                    and normalized_text
                    and normalized_text in event.text.casefold()
                )
            ]
        if not set(event_ids).issubset(request.wave.event_ids):
            raise ViewerRuntimeProtocolError(
                "Director meme candidate referenced an unknown event"
            )
        frame_count = (
            0 if request.wave.frame_bundle is None else len(request.wave.frame_bundle.frames)
        )
        if any(index < 0 or index >= frame_count for index in frame_indexes):
            raise ViewerRuntimeProtocolError(
                "Director meme candidate referenced an unknown frame"
            )
        runtime_spec = getattr(request.runtime, "canonical_runtime_spec", None)
        active_mode_id = getattr(runtime_spec, "active_mode_id", None)
        active_mode = next(
            (
                mode
                for mode in getattr(runtime_spec, "modes", ())
                if getattr(mode, "mode_id", None) == active_mode_id
            ),
            None,
        )
        namespace_id = getattr(active_mode, "namespace_id", None)
        if not isinstance(namespace_id, str) or not namespace_id:
            raise ViewerRuntimeProtocolError(
                "Director meme candidate is missing its trusted namespace"
            )
        identity = (
            f"{request.wave.session_id}\0{request.wave.audience_epoch}\0"
            f"{request.wave.observation_id}\0{text}"
        )
        candidate_id = (
            f"meme-candidate-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        )
        try:
            return MemeCandidate(
                candidate_id=candidate_id,
                room_id=request.wave.room_id,
                session_id=request.wave.session_id,
                audience_epoch=request.wave.audience_epoch,
                observation_id=request.wave.observation_id,
                namespace_id=namespace_id,
                text=text,
                evidence_event_ids=event_ids,
                evidence_frame_indexes=frame_indexes,
                created_at_ms=request.wave.created_at_ms,
            )
        except ValidationError as error:
            raise ViewerRuntimeProtocolError(
                "Director meme candidate violated the MemeCandidate contract: "
                f"{OpenAICompatibleViewerRuntimeProvider._validation_codes(error)}"
            ) from None

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
        raise ViewerRuntimeProtocolError("Director runtime context is not serializable")

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
