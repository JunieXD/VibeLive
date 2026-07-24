from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ViewerDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ViewerLifecycleState(StrEnum):
    NOT_JOINED = "not_joined"
    ACTIVE = "active"
    LEFT = "left"
    KICKED = "kicked"
    ENDED = "ended"
    REMOVED = "removed"


class ViewerInstanceVariant(ViewerDomainModel):
    activity_baseline: float = Field(default=0.5, ge=0, le=1)
    attention_span: float = Field(default=0.5, ge=0, le=1)
    social_initiative: float = Field(default=0.5, ge=0, le=1)
    reply_affinity: float = Field(default=0.5, ge=0, le=1)
    expression_length: float = Field(ge=0, le=1)
    skepticism: float = Field(ge=0, le=1)
    encouragement: float = Field(ge=0, le=1)
    meme_affinity: float = Field(ge=0, le=1)
    focus: str = Field(min_length=1, max_length=128)
    silence_tendency: float = Field(ge=0, le=1)
    stay_duration_tendency: float = Field(default=0.5, ge=0, le=1)
    rejoin_tendency: float = Field(default=0.5, ge=0, le=1)


class ViewerPrivateState(ViewerDomainModel):
    revision: int = Field(default=1, ge=1)
    published_event_ids: list[str] = Field(default_factory=list, max_length=64)
    direct_interaction_event_ids: list[str] = Field(default_factory=list, max_length=64)
    attention: list[str] = Field(default_factory=list, max_length=16)
    mood: dict[str, JsonValue] = Field(default_factory=dict)
    cooldown_until_ms: int | None = Field(default=None, ge=0)
    attention_strength: float = Field(default=0.5, ge=0, le=1)
    arousal: float = Field(default=0.0, ge=0, le=1)
    fatigue: float = Field(default=0.0, ge=0, le=1)
    engagement: float = Field(default=0.5, ge=0, le=1)
    last_spoke_at_ms: int | None = Field(default=None, ge=0)
    last_reacted_at_ms: int | None = Field(default=None, ge=0)
    current_thread_id: str | None = Field(default=None, max_length=128)
    current_target_viewer_id: str | None = Field(default=None, max_length=128)
    host_affinity: float = Field(default=0.0, ge=-1, le=1)
    peer_affinities: dict[str, float] = Field(default_factory=dict, max_length=32)
    silence_streak: int = Field(default=0, ge=0)
    speech_streak: int = Field(default=0, ge=0)


class ViewerInstance(ViewerDomainModel):
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    persona_id: str = Field(min_length=1, max_length=128)
    persona_revision: int = Field(ge=1)
    persona_content_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    ordinal: int = Field(ge=1, le=128)
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=64)
    avatar_seed: str = Field(min_length=1, max_length=128)
    color_seed: str = Field(min_length=1, max_length=128)
    locale: str = Field(default="zh-CN", min_length=2, max_length=32)
    variant: ViewerInstanceVariant
    private_state: ViewerPrivateState = Field(default_factory=ViewerPrivateState)
    viewer_sequence: int = Field(default=0, ge=0)
    lifecycle_state: ViewerLifecycleState = ViewerLifecycleState.ACTIVE
    presence_revision: int = Field(default=1, ge=1)
    moderation_revision: int = Field(default=1, ge=1)
    behavior_revision: int = Field(default=1, ge=1)
    joined_at_ms: int | None = Field(default=None, ge=0)
    last_left_at_ms: int | None = Field(default=None, ge=0)
    join_count: int = Field(default=0, ge=0)
    muted_until_ms: int | None = Field(default=None, ge=0)
    mute_reason: str | None = Field(default=None, max_length=256)
    kicked_at_ms: int | None = Field(default=None, ge=0)
    kick_reason: str | None = Field(default=None, max_length=256)
    created_at_ms: int = Field(ge=0)
    removed_at_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ViewerInstance":
        terminal = {
            ViewerLifecycleState.KICKED,
            ViewerLifecycleState.ENDED,
            ViewerLifecycleState.REMOVED,
        }
        if self.lifecycle_state is ViewerLifecycleState.ACTIVE:
            if self.joined_at_ms is None:
                raise ValueError("an active Viewer requires joined_at_ms")
            if self.removed_at_ms is not None:
                raise ValueError("an active Viewer cannot have removed_at_ms")
        if self.lifecycle_state is ViewerLifecycleState.KICKED and self.kicked_at_ms is None:
            raise ValueError("a kicked Viewer requires kicked_at_ms")
        if self.lifecycle_state in terminal and self.removed_at_ms is None:
            raise ValueError("a terminal Viewer requires removed_at_ms")
        if self.join_count == 0 and self.joined_at_ms is not None:
            raise ValueError("a joined Viewer requires join_count")
        for value in (
            self.joined_at_ms,
            self.last_left_at_ms,
            self.kicked_at_ms,
            self.removed_at_ms,
        ):
            if value is not None and value < self.created_at_ms:
                raise ValueError("Viewer lifecycle timestamps cannot precede created_at_ms")
        if self.muted_until_ms is None and self.mute_reason is not None:
            raise ValueError("mute_reason requires muted_until_ms")
        return self

    def is_active(self) -> bool:
        return self.lifecycle_state is ViewerLifecycleState.ACTIVE

    def is_muted(self, now_ms: int) -> bool:
        return self.muted_until_ms is not None and self.muted_until_ms > now_ms
