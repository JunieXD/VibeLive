from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from advx_backend.contracts.protocol import PROTOCOL_VERSION, TRACE_SCHEMA_VERSION
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
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


class DirectorBudgetTrace(DebugContractModel):
    minimum: int = Field(ge=0, le=32)
    maximum: int = Field(ge=0, le=32)
    available_viewer_ids: list[str] = Field(default_factory=list, max_length=32)
    forced_viewer_ids: list[str] = Field(default_factory=list, max_length=32)


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
    director_budget: DirectorBudgetTrace
    director_decision: CrowdDecision
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    viewer_sequence: int = Field(ge=1)
    persona_revision: int = Field(ge=1)
    instance_variant: ViewerInstanceVariant
    public_context_event_ids: list[str] = Field(default_factory=list, max_length=512)
    private_state_event_ids: list[str] = Field(default_factory=list, max_length=128)
    memory: MemoryReferenceTrace
    frame_hashes: list[str] = Field(default_factory=list, max_length=32)
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
                "memory_ids": list(memory_ids)
                if isinstance(memory_ids, (list, tuple))
                else [],
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
    triggers: list[ObservationTrigger] = Field(min_length=1, max_length=4)
    event_ids: list[str] = Field(default_factory=list, max_length=128)
    trigger_event_ids: list[str] = Field(default_factory=list, max_length=128)
    frame_hashes: list[str] = Field(default_factory=list, max_length=32)
    memory: MemoryReferenceTrace
    director_status: ObservationWaveStatus
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
    protocol_version: Literal[2] = PROTOCOL_VERSION
    redacted: Literal[True] = True
    session_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    accepting_results: bool
    config: CanonicalRuntimeSpec
    pool: DebugViewerPoolSnapshot
    waves: list[ObservationWaveTrace] = Field(default_factory=list, max_length=1024)
    director_budgets: list[DirectorBudgetTrace] = Field(
        default_factory=list,
        max_length=1024,
    )
    queue: DebugQueueSnapshot | None = None
    telemetry: ViewerRuntimeTelemetry | None = None
    context_refs: DebugContextReferences = Field(default_factory=DebugContextReferences)
    memory: DebugMemorySnapshot = Field(default_factory=DebugMemorySnapshot)
    memes: DebugMemeSnapshot = Field(default_factory=DebugMemeSnapshot)
    history: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=1024)
    unavailable: list[str] = Field(default_factory=list, max_length=16)
