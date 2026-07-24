from pydantic import BaseModel, ConfigDict, Field, model_validator

from advx_backend.domain.crowd_decision import DecisionSource


class SceneAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=128)
    salience: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    emotional_intensity: float = Field(ge=0, le=1)
    topics: list[str] = Field(default_factory=list, max_length=32)
    emotional_tone: list[str] = Field(default_factory=list, max_length=16)
    replyable_event_ids: list[str] = Field(default_factory=list, max_length=128)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=128)
    evidence_frame_indexes: list[int] = Field(default_factory=list, max_length=32)
    suggested_reaction_types: list[str] = Field(default_factory=list, max_length=32)
    maximum_responses: int = Field(ge=0, le=32)
    reason_codes: list[str] = Field(default_factory=list, max_length=32)
    decision_source: DecisionSource = DecisionSource.AUTONOMOUS
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_assessment(self) -> "SceneAssessment":
        if self.expires_at_ms <= self.created_at_ms:
            raise ValueError("expires_at_ms must be later than created_at_ms")
        if len(set(self.replyable_event_ids)) != len(self.replyable_event_ids):
            raise ValueError("replyable_event_ids must be unique")
        if any(index < 0 for index in self.evidence_frame_indexes):
            raise ValueError("evidence frame indexes must not be negative")
        return self
