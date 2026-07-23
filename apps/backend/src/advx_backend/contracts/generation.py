from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advx_backend.contracts.audience import AudienceMember, AudienceMemory
from advx_backend.contracts.events import RoomEvent
from advx_backend.contracts.session import AudienceModeConfiguration, PersonaTemplate


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrameRef(BaseModel):
    frame_id: str = Field(min_length=1)
    created_at_ms: int = Field(ge=0)
    mime_type: str
    data_ref: str


class Observation(BaseModel):
    session_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    created_at_ms: int = Field(ge=0)
    frames: list[FrameRef] = Field(default_factory=list)
    room_events: list[RoomEvent] = Field(default_factory=list)
    user_context: dict[str, str] = Field(default_factory=dict)


class AudienceContext(BaseModel):
    member: AudienceMember
    memories: list[AudienceMemory] = Field(default_factory=list)
    session_state: dict[str, Any] = Field(default_factory=dict)


class GenerationRequest(BaseModel):
    request_id: str = Field(min_length=1)
    observation: Observation
    audiences: list[AudienceContext] = Field(min_length=1)


class BarrageCandidate(BaseModel):
    audience_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=200)


class GenerationResult(BaseModel):
    request_id: str = Field(min_length=1)
    candidates: list[BarrageCandidate] = Field(default_factory=list)


class MemeCandidate(StrictContractModel):
    mode_id: str = Field(min_length=1, max_length=128)
    mode_namespace_id: str = Field(min_length=1, max_length=128)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=128)
    text: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=32)
    created_at_ms: int = Field(ge=0)


class ViewerPoolEntry(StrictContractModel):
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    persona_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class DirectorRequest(StrictContractModel):
    request_id: str = Field(min_length=1, max_length=128)
    observation: Observation
    mode: AudienceModeConfiguration
    viewer_pool: list[ViewerPoolEntry] = Field(max_length=32)
    recent_room_state: list[RoomEvent] = Field(default_factory=list, max_length=256)
    active_memes: list[MemeCandidate] = Field(default_factory=list, max_length=128)


class CrowdDecision(StrictContractModel):
    decision_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    observation_id: str = Field(min_length=1, max_length=128)
    selected_viewer_instance_ids: list[str] = Field(default_factory=list, max_length=32)
    intent: str = Field(min_length=1, max_length=128)
    event_level: str = Field(min_length=1, max_length=64)
    silent: bool
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=128)
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    meme_candidates: list[MemeCandidate] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_selection(self) -> "CrowdDecision":
        selected = self.selected_viewer_instance_ids
        if len(selected) != len(set(selected)):
            raise ValueError("selected_viewer_instance_ids cannot contain duplicates")
        if self.silent and selected:
            raise ValueError("an explicit silent decision cannot select viewer instances")
        if not self.silent and not selected:
            raise ValueError("a non-silent decision must select viewer instances")
        if self.expires_at_ms <= self.created_at_ms:
            raise ValueError("expires_at_ms must be later than created_at_ms")
        return self


class ViewerGenerationRequest(StrictContractModel):
    request_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    observation_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    persona_id: str = Field(min_length=1, max_length=128)
    observation: Observation
    compiled_persona: PersonaTemplate
    instance_state: dict[str, Any] = Field(default_factory=dict)
    persona_memory_revision: int = Field(ge=0)
    persona_memories: list[AudienceMemory] = Field(default_factory=list, max_length=256)
    active_memes: list[MemeCandidate] = Field(default_factory=list, max_length=128)


class ViewerComment(StrictContractModel):
    text: str = Field(min_length=1, max_length=200)


class ViewerGenerationResult(StrictContractModel):
    request_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    observation_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    persona_id: str = Field(min_length=1, max_length=128)
    silent: bool
    comments: list[ViewerComment] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_silence(self) -> "ViewerGenerationResult":
        if self.silent and self.comments:
            raise ValueError("a silent viewer result cannot contain comments")
        return self
