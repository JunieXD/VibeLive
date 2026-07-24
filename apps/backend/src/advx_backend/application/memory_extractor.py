import asyncio
import hashlib
import json
from collections.abc import Sequence
from typing import Final, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from advx_backend.application.ai_call_logging import (
    AiCallLifecycle,
    AiCallScope,
    AiCallSink,
    build_http_response_summary,
    build_openai_request_summary,
)
from advx_backend.application.ports.memory import MemoryEvidence, RoomMemoryCandidate
from advx_backend.contracts.debug import AiCallRole
from advx_backend.domain.memory import RoomMemoryType
from advx_backend.providers.model.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleHttpError,
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
    OpenAICompatibleTimeoutError,
    OpenAICompatibleTransportError,
)
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
    ViewerRuntimeProtocolError,
    ViewerRuntimeProviderBlockedError,
    ViewerRuntimeProviderError,
)


class _MemoryOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_type: RoomMemoryType
    content: str = Field(min_length=1, max_length=4_000)
    evidence_event_ids: list[str] = Field(min_length=1, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=32)
    importance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must not be blank")
        return normalized

    @field_validator("evidence_event_ids")
    @classmethod
    def validate_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item.strip() for item in value):
            raise ValueError("evidence event IDs must be unique and non-blank")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 128 for item in normalized):
            raise ValueError("tags must be non-blank and at most 128 characters")
        return normalized


class _MemoryExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: list[_MemoryOutputModel] = Field(default_factory=list, max_length=32)


_MEMORY_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "memory_type",
                    "content",
                    "evidence_event_ids",
                    "tags",
                    "importance",
                    "confidence",
                ],
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": [
                            "user_preference",
                            "real_world_fact",
                            "room_lore",
                            "shared_experience",
                        ],
                    },
                    "content": {"type": "string", "minLength": 1, "maxLength": 4_000},
                    "evidence_event_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 128,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128},
                    },
                    "tags": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128},
                    },
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}
_MEMORY_SYSTEM_PROMPT: Final = (
    "Extract zero or more durable room memory candidates from public events only. "
    "Evidence IDs must come from the supplied events. Do not infer missing evidence. "
    "User preferences and real-world facts still require downstream non-AI evidence validation. "
    "Return only the required JSON object."
)


class RoomMemoryExtractor(Protocol):
    async def extract(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        events: Sequence[MemoryEvidence],
        current_revision: int,
    ) -> tuple[RoomMemoryCandidate, ...]: ...


class OpenAICompatibleMemoryExtractor:
    """One low-priority, bounded memory extraction call per observation wave."""

    def __init__(
        self,
        config: OpenAICompatibleViewerRuntimeConfig,
        *,
        client: httpx.AsyncClient | None = None,
        max_concurrency: int = 1,
        ai_call_sink: AiCallSink | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        self.config = config
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None
        self._provider = self._role_provider()
        self._ai_call_sink = ai_call_sink
        self._slots = asyncio.Semaphore(max_concurrency)
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def extract(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        events: Sequence[MemoryEvidence],
        current_revision: int,
    ) -> tuple[RoomMemoryCandidate, ...]:
        if not room_id or not session_id or audience_epoch < 1 or current_revision < 0:
            raise ViewerRuntimeProtocolError("Memory extraction scope is invalid")
        event_ids = tuple(event.event_id for event in events)
        if len(set(event_ids)) != len(event_ids):
            raise ViewerRuntimeProtocolError("Memory extraction event IDs must be unique")
        if any(event.room_id != room_id for event in events):
            raise ViewerRuntimeProtocolError("Memory extraction events belong to another room")

        context = {
            "room_id": room_id,
            "session_id": session_id,
            "audience_epoch": audience_epoch,
            "current_revision": current_revision,
            "public_events": [
                {
                    "event_id": event.event_id,
                    "source_type": event.source_type,
                    "occurred_at_ms": event.occurred_at_ms,
                    "summary": event.summary,
                }
                for event in events
            ],
        }
        payload = {
            "model": self.config.provider.memory_model,
            "messages": [
                {"role": "system", "content": _MEMORY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        context,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "stream": False,
            "n": 1,
            "max_tokens": 4_096,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "room_memory_candidates",
                    "strict": True,
                    "schema": _MEMORY_SCHEMA,
                },
            },
        }
        correlation_source = json.dumps(
            {
                "session_id": session_id,
                "audience_epoch": audience_epoch,
                "current_revision": current_revision,
                "event_ids": list(event_ids),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        lifecycle = AiCallLifecycle(
            sink=self._ai_call_sink,
            role=AiCallRole.MEMORY,
            correlation_id=(
                "memory-"
                f"{hashlib.sha256(correlation_source.encode('utf-8')).hexdigest()[:32]}"
            ),
            provider="openai_compatible",
            model_id=self.config.provider.memory_model,
            endpoint=f"{self.config.base_url.rstrip('/')}/chat/completions",
            scope=AiCallScope(
                room_id=room_id,
                session_id=session_id,
                audience_epoch=audience_epoch,
            ),
        )
        try:
            if self._closed:
                raise ViewerRuntimeProviderError("Memory extractor is closed")
            if self._provider is None:
                raise ViewerRuntimeProviderBlockedError(
                    "Model provider credentials are not configured"
                )
            lifecycle.sent(build_openai_request_summary(payload))
            async with self._slots:
                try:
                    response = await self._provider._send(
                        "POST",
                        self._provider._chat_completions_endpoint(),
                        payload=payload,
                    )
                except OpenAICompatibleProviderError as error:
                    if (
                        isinstance(error, OpenAICompatibleHttpError)
                        and error.response is not None
                    ):
                        lifecycle.received(
                            build_http_response_summary(error.response)
                        )
                    status_code = (
                        error.status_code
                        if isinstance(error, OpenAICompatibleHttpError)
                        else None
                    )
                    raise ViewerRuntimeProviderError(
                        str(error),
                        status_code=status_code,
                        retryable=(
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
                        ),
                        retry_after_seconds=(
                            error.retry_after_seconds
                            if isinstance(error, OpenAICompatibleHttpError)
                            else None
                        ),
                    ) from None
            lifecycle.received(build_http_response_summary(response))
            output = self._structured_output(response)
            try:
                parsed = _MemoryExtractionOutput.model_validate(output)
            except ValidationError:
                raise ViewerRuntimeProtocolError(
                    "Memory response violated the candidate contract"
                ) from None

            allowed_evidence = set(event_ids)
            candidates: list[RoomMemoryCandidate] = []
            for index, item in enumerate(parsed.candidates):
                if not set(item.evidence_event_ids).issubset(allowed_evidence):
                    raise ViewerRuntimeProtocolError(
                        "Memory candidate referenced a non-public event"
                    )
                candidates.append(
                    self._candidate(
                        room_id=room_id,
                        session_id=session_id,
                        audience_epoch=audience_epoch,
                        current_revision=current_revision,
                        index=index,
                        output=item,
                    )
                )
            lifecycle.succeeded(
                {
                    "candidate_count": len(parsed.candidates),
                    "candidates": [
                        {
                            "memory_type": item.memory_type.value,
                            "evidence_event_ids": item.evidence_event_ids,
                            "importance": item.importance,
                            "confidence": item.confidence,
                            "content_ref": {
                                "chars": len(item.content),
                                "sha256": hashlib.sha256(
                                    item.content.encode("utf-8")
                                ).hexdigest(),
                            },
                        }
                        for item in parsed.candidates
                    ],
                }
            )
            return tuple(candidates)
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
            if self._provider is not None:
                await self._provider.aclose()
            if self._owns_client:
                await self._client.aclose()

    def _role_provider(self) -> OpenAICompatibleProvider | None:
        if not self.config.api_key:
            return None
        return OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=self.config.base_url,
                model=self.config.provider.memory_model,
                api_key=self.config.api_key,
                request_timeout_seconds=self.config.request_timeout_seconds,
            ),
            client=self._client,
        )

    @staticmethod
    def _candidate(
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        current_revision: int,
        index: int,
        output: _MemoryOutputModel,
    ) -> RoomMemoryCandidate:
        identity = json.dumps(
            {
                "room_id": room_id,
                "session_id": session_id,
                "audience_epoch": audience_epoch,
                "base_revision": current_revision,
                "index": index,
                "memory_type": output.memory_type.value,
                "content": output.content,
                "evidence_event_ids": output.evidence_event_ids,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return RoomMemoryCandidate(
            candidate_id=f"memory-candidate-{digest[:24]}",
            room_id=room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            idempotency_key=f"memory-extract-{digest}",
            base_revision=current_revision,
            memory_id=f"memory-{digest[:24]}",
            memory_type=output.memory_type,
            content=output.content,
            evidence_event_ids=tuple(output.evidence_event_ids),
            tags=tuple(output.tags),
            origin="extracted",
            importance=output.importance,
            confidence=output.confidence,
        )

    @staticmethod
    def _structured_output(response: httpx.Response) -> dict[str, object]:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise ViewerRuntimeProtocolError("Memory response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise ViewerRuntimeProtocolError("Memory response was not a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ViewerRuntimeProtocolError("Memory response must contain exactly one choice")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ViewerRuntimeProtocolError("Memory response choice did not contain a message")
        finish_reason = choice.get("finish_reason")
        if finish_reason not in {None, "stop"}:
            normalized_reason = (
                finish_reason
                if finish_reason in {"length", "content_filter", "tool_calls"}
                else "unexpected"
            )
            raise ViewerRuntimeProtocolError(
                f"Memory response did not finish normally: {normalized_reason}"
            )
        content = choice["message"].get("content")
        if not isinstance(content, str):
            raise ViewerRuntimeProtocolError("Memory response message did not contain JSON text")
        try:
            output = json.loads(content)
        except json.JSONDecodeError:
            raise ViewerRuntimeProtocolError(
                "Memory structured output was not valid JSON"
            ) from None
        if not isinstance(output, dict):
            raise ViewerRuntimeProtocolError("Memory structured output was not a JSON object")
        return output


__all__ = ["OpenAICompatibleMemoryExtractor", "RoomMemoryExtractor"]
