from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ViewerDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ViewerLifecycleState(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class ViewerInstanceVariant(ViewerDomainModel):
    expression_length: float = Field(ge=0, le=1)
    skepticism: float = Field(ge=0, le=1)
    encouragement: float = Field(ge=0, le=1)
    meme_affinity: float = Field(ge=0, le=1)
    focus: str = Field(min_length=1, max_length=128)
    silence_tendency: float = Field(ge=0, le=1)


class ViewerPrivateState(ViewerDomainModel):
    revision: int = Field(default=1, ge=1)
    published_event_ids: list[str] = Field(default_factory=list, max_length=64)
    direct_interaction_event_ids: list[str] = Field(default_factory=list, max_length=64)
    attention: list[str] = Field(default_factory=list, max_length=16)
    mood: dict[str, JsonValue] = Field(default_factory=dict)
    cooldown_until_ms: int | None = Field(default=None, ge=0)


class ViewerInstance(ViewerDomainModel):
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    persona_id: str = Field(min_length=1, max_length=128)
    persona_revision: int = Field(ge=1)
    ordinal: int = Field(ge=1, le=32)
    display_name: str = Field(min_length=1, max_length=64)
    variant: ViewerInstanceVariant
    private_state: ViewerPrivateState = Field(default_factory=ViewerPrivateState)
    viewer_sequence: int = Field(default=0, ge=0)
    lifecycle_state: ViewerLifecycleState = ViewerLifecycleState.ACTIVE
    created_at_ms: int = Field(ge=0)
    removed_at_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ViewerInstance":
        if self.lifecycle_state is ViewerLifecycleState.ACTIVE and self.removed_at_ms is not None:
            raise ValueError("an active Viewer cannot have removed_at_ms")
        if self.lifecycle_state is ViewerLifecycleState.REMOVED and self.removed_at_ms is None:
            raise ValueError("a removed Viewer requires removed_at_ms")
        if self.removed_at_ms is not None and self.removed_at_ms < self.created_at_ms:
            raise ValueError("removed_at_ms must not precede created_at_ms")
        return self
