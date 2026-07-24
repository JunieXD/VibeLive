from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.domain.viewer import ViewerInstance, ViewerLifecycleState


class AudienceMember(BaseModel):
    audience_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=64)
    avatar_ref: str | None = None
    personality: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    speaking_style: dict[str, Any] = Field(default_factory=dict)
    relationships: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AudienceMemory(BaseModel):
    memory_id: str = Field(min_length=1)
    audience_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_event_ids: list[str] = Field(default_factory=list)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)


class AudienceContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ViewerSnapshot(AudienceContractModel):
    viewer_instance_id: str
    username: str
    display_name: str
    avatar_seed: str
    color_seed: str
    persona_id: str
    persona_display_name: str
    presence_state: ViewerLifecycleState
    joined_at_ms: int | None
    last_left_at_ms: int | None
    join_count: int = Field(ge=0)
    muted_until_ms: int | None
    viewer_sequence: int = Field(ge=0)
    presence_revision: int = Field(ge=1)
    moderation_revision: int = Field(ge=1)

    @classmethod
    def from_domain(
        cls,
        viewer: ViewerInstance,
        *,
        persona_display_name: str,
    ) -> "ViewerSnapshot":
        return cls(
            viewer_instance_id=viewer.viewer_instance_id,
            username=viewer.username,
            display_name=viewer.display_name,
            avatar_seed=viewer.avatar_seed,
            color_seed=viewer.color_seed,
            persona_id=viewer.persona_id,
            persona_display_name=persona_display_name,
            presence_state=viewer.lifecycle_state,
            joined_at_ms=viewer.joined_at_ms,
            last_left_at_ms=viewer.last_left_at_ms,
            join_count=viewer.join_count,
            muted_until_ms=viewer.muted_until_ms,
            viewer_sequence=viewer.viewer_sequence,
            presence_revision=viewer.presence_revision,
            moderation_revision=viewer.moderation_revision,
        )


class SessionAudienceSnapshot(AudienceContractModel):
    session_id: str
    room_id: str
    audience_epoch: int = Field(ge=1)
    population_revision: int = Field(ge=1)
    target_concurrent_viewers: int = Field(ge=1, le=32)
    active_count: int = Field(ge=0)
    viewers: list[ViewerSnapshot] = Field(default_factory=list, max_length=128)


class MuteViewerRequest(AudienceContractModel):
    command_id: str = Field(min_length=1, max_length=128)
    duration_ms: int = Field(ge=1_000, le=3_600_000)
    reason: str | None = Field(default=None, max_length=256)


class ViewerCommandRequest(AudienceContractModel):
    command_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=256)


ViewerEventType = Literal[
    "viewer.joined",
    "viewer.left",
    "viewer.rejoined",
    "viewer.muted",
    "viewer.unmuted",
    "viewer.kicked",
]


class ViewerPresenceEvent(AudienceContractModel):
    type: ViewerEventType
    protocol_version: Literal[3] = PROTOCOL_VERSION
    session_id: str
    audience_epoch: int = Field(ge=1)
    population_revision: int = Field(ge=1)
    occurred_at_ms: int = Field(ge=0)
    viewer: ViewerSnapshot


class AudienceSnapshotEvent(AudienceContractModel):
    type: Literal["audience.snapshot"] = "audience.snapshot"
    protocol_version: Literal[3] = PROTOCOL_VERSION
    audience: SessionAudienceSnapshot
