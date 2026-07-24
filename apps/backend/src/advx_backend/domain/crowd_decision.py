from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CrowdDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionSource(StrEnum):
    DIRECTOR = "director"
    FALLBACK = "fallback"
    AUTONOMOUS = "autonomous"


class CrowdDecision(CrowdDomainModel):
    decision_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=128)
    selected_viewer_ids: list[str] = Field(default_factory=list, max_length=32)
    reason_codes: list[str] = Field(default_factory=list, max_length=32)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=128)
    evidence_frame_indexes: list[int] = Field(default_factory=list, max_length=32)
    decision_source: DecisionSource = DecisionSource.DIRECTOR
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_decision(self) -> "CrowdDecision":
        if self.expires_at_ms <= self.created_at_ms:
            raise ValueError("expires_at_ms must be later than created_at_ms")
        if len(set(self.selected_viewer_ids)) != len(self.selected_viewer_ids):
            raise ValueError("selected_viewer_ids must be unique")
        if any(index < 0 for index in self.evidence_frame_indexes):
            raise ValueError("evidence frame indexes must not be negative")
        return self
