from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from advx_backend.contracts.protocol import PROTOCOL_VERSION, TRACE_SCHEMA_VERSION
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ViewerRequestTriggerContext,
    ViewerRuntimeTelemetry,
)
from advx_backend.domain.crowd_decision import CrowdDecision, DecisionSource
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.observation_wave import ObservationTrigger
from advx_backend.domain.viewer import ViewerInstance, ViewerInstanceVariant


class DebugContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TraceResponseStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    SILENCE = "silence"
    PUBLISHED = "published"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    STALE = "stale"
    FAILED = "failed"


class ObservationWaveStatus(StrEnum):
    COMPLETED = "completed"
    EMPTY = "empty"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemoryReferenceTrace(DebugContractModel):
    room_id: str = Field(min_length=1, max_length=128)
    memory_revision: int = Field(ge=0)
    memory_ids: list[str] = Field(default_factory=list, max_length=128)


class PromptManifest(DebugContractModel):
    template_id: str = Field(min_length=1, max_length=128)
    template_revision: int = Field(ge=1)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sections: list[str] = Field(default_factory=list, max_length=64)


class ProviderTrace(DebugContractModel):
    provider_role: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=256)
    queued_at_ms: int = Field(ge=0)
    dispatched_at_ms: int | None = Field(default=None, ge=0)
    completed_at_ms: int | None = Field(default=None, ge=0)


class ValidationTrace(DebugContractModel):
    accepted: bool
    codes: list[str] = Field(default_factory=list, max_length=64)


class SideEffectTrace(DebugContractModel):
    published_barrage_id: str | None = None
    published_barrage_ids: list[str] = Field(default_factory=list, max_length=3)
    memory_candidate_ids: list[str] = Field(default_factory=list, max_length=128)
    meme_candidate: MemeCandidate | None = None


class ViewerRequestTrace(DebugContractModel):
    trace_kind: Literal["viewer_request"] = "viewer_request"
    trace_schema_version: Literal[1] = TRACE_SCHEMA_VERSION
    trace_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_id: str = Field(min_length=1, max_length=128)
    decision: CrowdDecision
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    viewer_sequence: int = Field(ge=1)
    persona_revision: int = Field(ge=1)
    instance_variant: ViewerInstanceVariant
    public_context_event_ids: list[str] = Field(default_factory=list, max_length=512)
    private_state_event_ids: list[str] = Field(default_factory=list, max_length=128)
    memory: MemoryReferenceTrace
    frame_hashes: list[str] = Field(default_factory=list, max_length=60)
    prompt_manifest: PromptManifest
    provider: ProviderTrace
    response_status: TraceResponseStatus
    validation: ValidationTrace
    retry_count: int = Field(default=0, ge=0, le=1)
    stale_or_cancel_reason: str | None = Field(default=None, max_length=256)
    side_effects: SideEffectTrace = Field(default_factory=SideEffectTrace)

    @field_validator("memory", mode="before")
    @classmethod
    def redact_memory(cls, value: object) -> object:
        if isinstance(value, MemoryReferenceTrace):
            return value
        room_id = getattr(value, "room_id", None)
        memory_revision = getattr(value, "memory_revision", None)
        memory_ids = getattr(value, "memory_ids", None)
        if isinstance(room_id, str) and isinstance(memory_revision, int):
            return {
                "room_id": room_id,
                "memory_revision": memory_revision,
                "memory_ids": list(memory_ids) if isinstance(memory_ids, (list, tuple)) else [],
            }
        return value


class ObservationWaveTrace(DebugContractModel):
    trace_kind: Literal["observation_wave"] = "observation_wave"
    trace_schema_version: Literal[1] = TRACE_SCHEMA_VERSION
    trace_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_id: str = Field(min_length=1, max_length=128)
    created_at_ms: int = Field(ge=0)
    deadline_at_ms: int = Field(gt=0)
    triggers: list[ObservationTrigger] = Field(min_length=1, max_length=5)
    event_ids: list[str] = Field(default_factory=list, max_length=128)
    trigger_event_ids: list[str] = Field(default_factory=list, max_length=128)
    frame_hashes: list[str] = Field(default_factory=list, max_length=60)
    memory: MemoryReferenceTrace
    status: ObservationWaveStatus
    selected_viewer_ids: list[str] = Field(default_factory=list, max_length=32)
    decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    decision_source: DecisionSource | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=32)
    failure_reason: str | None = Field(default=None, max_length=256)


DebugTrace = ViewerRequestTrace | ObservationWaveTrace


class TraceQuery(DebugContractModel):
    room_id: str | None = Field(default=None, min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    observation_id: str | None = Field(default=None, min_length=1, max_length=128)
    viewer_instance_id: str | None = Field(default=None, min_length=1, max_length=128)
    response_status: TraceResponseStatus | None = None
    cursor: str | None = Field(default=None, min_length=1, max_length=512)
    limit: int = Field(default=100, ge=1, le=1000)


class TraceQueryResponse(DebugContractModel):
    items: list[ViewerRequestTrace]
    waves: list[ObservationWaveTrace] = Field(default_factory=list)
    next_cursor: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AiCallRole(StrEnum):
    LEGACY_DIRECTOR = "legacy_director"
    VIEWER = "viewer"
    VISUAL_SUMMARY = "visual_summary"
    HISTORY_SUMMARY = "history_summary"
    MEMORY = "memory"
    ASR = "asr"


class AiCallStatus(StrEnum):
    PREPARING = "preparing"
    SENT = "sent"
    STREAMING = "streaming"
    RECEIVED = "received"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class AiCallTimelineEvent(DebugContractModel):
    stage: str = Field(min_length=1, max_length=64)
    at_ms: int = Field(ge=0)
    detail: JsonValue = None


class AiCallRequestSummary(DebugContractModel):
    wire_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    wire_bytes: int | None = Field(default=None, ge=0)
    schema_name: str | None = Field(default=None, min_length=1, max_length=128)
    max_output_tokens: int | None = Field(default=None, ge=1)
    input_preview: JsonValue = None
    redacted_fields: list[str] = Field(default_factory=list, max_length=128)


class AiCallResponseSummary(DebugContractModel):
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=256)
    body_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    body_bytes: int | None = Field(default=None, ge=0)
    finish_reason: str | None = Field(default=None, min_length=1, max_length=128)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    model_output: str | None = None
    parsed_output: JsonValue = None


class AiCallError(DebugContractModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1024)
    http_status: int | None = Field(default=None, ge=100, le=599)
    retryable: bool = False


class AiCallTrace(DebugContractModel):
    call_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    role: AiCallRole
    status: AiCallStatus
    provider: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    endpoint: str = Field(min_length=1, max_length=2048)
    room_id: str | None = Field(default=None, min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    audience_epoch: int | None = Field(default=None, ge=1)
    observation_id: str | None = Field(default=None, min_length=1, max_length=128)
    generation_request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    viewer_instance_id: str | None = Field(default=None, min_length=1, max_length=128)
    trigger_context: ViewerRequestTriggerContext | None = None
    utterance_id: str | None = Field(default=None, min_length=1, max_length=128)
    started_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    completed_at_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    timeline: list[AiCallTimelineEvent] = Field(default_factory=list, max_length=256)
    request: AiCallRequestSummary | None = None
    response: AiCallResponseSummary | None = None
    error: AiCallError | None = None
    redacted: Literal[True] = True


class AiCallListItem(DebugContractModel):
    """Compact AI call metadata used by the paginated log list."""

    call_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    role: AiCallRole
    status: AiCallStatus
    model_id: str = Field(min_length=1, max_length=256)
    trigger_context: ViewerRequestTriggerContext | None = None
    started_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    duration_ms: int | None = Field(default=None, ge=0)

    @classmethod
    def from_trace(cls, trace: AiCallTrace) -> "AiCallListItem":
        return cls(
            call_id=trace.call_id,
            correlation_id=trace.correlation_id,
            role=trace.role,
            status=trace.status,
            model_id=trace.model_id,
            trigger_context=trace.trigger_context,
            started_at_ms=trace.started_at_ms,
            updated_at_ms=trace.updated_at_ms,
            duration_ms=trace.duration_ms,
        )


class AiCallQuery(DebugContractModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    role: AiCallRole | None = None
    status: AiCallStatus | None = None
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)
    limit: int = Field(default=100, ge=1, le=1000)


class AiCallQueryResponse(DebugContractModel):
    items: list[AiCallListItem]
    next_cursor: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AiCallImagePreview(DebugContractModel):
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    data_url: str = Field(min_length=1)


class DebugQueueSnapshot(DebugContractModel):
    depth: int | None = Field(default=None, ge=0)
    capacity: int | None = Field(default=None, ge=1)


class DebugContextReferences(DebugContractModel):
    event_ids: list[str] = Field(default_factory=list, max_length=1024)
    frame_hashes: list[str] = Field(default_factory=list, max_length=128)
    memory_ids: list[str] = Field(default_factory=list, max_length=512)


class DebugMemorySnapshot(DebugContractModel):
    revision: int = Field(default=0, ge=0)
    ids: list[str] = Field(default_factory=list, max_length=512)


class DebugMemeSnapshot(DebugContractModel):
    ids: list[str] = Field(default_factory=list, max_length=512)
    candidate_ids: list[str] = Field(default_factory=list, max_length=512)


class RuntimeAgentDebugSnapshot(DebugContractModel):
    """Optional agent-owned diagnostics; DebugService never keeps a second counter set."""

    queue: DebugQueueSnapshot | None = None
    telemetry: ViewerRuntimeTelemetry | None = None
    context_refs: DebugContextReferences | None = None
    memory: DebugMemorySnapshot | None = None
    memes: DebugMemeSnapshot | None = None
    history: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=1024)


class DebugViewerPoolSnapshot(DebugContractModel):
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    mode_id: str = Field(min_length=1, max_length=128)
    session_seed: str = Field(min_length=1, max_length=256)
    viewers: list[ViewerInstance] = Field(default_factory=list, max_length=32)


class DebugRuntimeSnapshot(DebugContractModel):
    protocol_version: Literal[3] = PROTOCOL_VERSION
    redacted: Literal[True] = True
    session_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    accepting_results: bool
    config: CanonicalRuntimeSpec
    pool: DebugViewerPoolSnapshot
    waves: list[ObservationWaveTrace] = Field(default_factory=list, max_length=1024)
    queue: DebugQueueSnapshot | None = None
    telemetry: ViewerRuntimeTelemetry | None = None
    context_refs: DebugContextReferences = Field(default_factory=DebugContextReferences)
    memory: DebugMemorySnapshot = Field(default_factory=DebugMemorySnapshot)
    memes: DebugMemeSnapshot = Field(default_factory=DebugMemeSnapshot)
    history: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=1024)
    unavailable: list[str] = Field(default_factory=list, max_length=16)
