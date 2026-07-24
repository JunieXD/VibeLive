from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from advx_backend.domain.observation_wave import MAX_FRAME_BUNDLE_SIZE


class MemeDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemeCandidateOutcome(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ModeMemeState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"
    REVOKED = "revoked"


class MemeCandidate(MemeDomainModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=128)
    namespace_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_event_ids: list[str] = Field(min_length=1, max_length=128)
    evidence_frame_indexes: list[int] = Field(
        default_factory=list,
        max_length=MAX_FRAME_BUNDLE_SIZE,
    )
    outcome: MemeCandidateOutcome = MemeCandidateOutcome.PENDING
    created_at_ms: int = Field(ge=0)


class ModeMeme(MemeDomainModel):
    meme_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    namespace_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)
    intensity: float = Field(default=0.5, ge=0, le=1)
    source_candidate_id: str = Field(min_length=1, max_length=128)
    state: ModeMemeState = ModeMemeState.ACTIVE
    pinned: bool = False
    use_count: int = Field(default=0, ge=0)
    revision: int = Field(ge=1)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "ModeMeme":
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated_at_ms must not precede created_at_ms")
        return self
