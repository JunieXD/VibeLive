from typing import Any

from pydantic import BaseModel, Field

from advx_backend.contracts.audience import AudienceMember, AudienceMemory
from advx_backend.contracts.events import RoomEvent


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
