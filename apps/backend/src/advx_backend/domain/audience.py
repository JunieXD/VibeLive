from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class AudienceOrigin(StrEnum):
    PRESET = "preset"
    CUSTOM = "custom"


class MemoryOrigin(StrEnum):
    EXTRACTED = "extracted"
    USER = "user"


class MemoryState(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class RelationshipUpdatedBy(StrEnum):
    MEMORY = "memory"
    USER = "user"


class PersistenceModel(BaseModel):
    model_config = ConfigDict(frozen=True)


MemoryTag = Annotated[str, Field(min_length=1, max_length=64)]


class AudienceProfile(PersistenceModel):
    audience_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=64)
    avatar_ref: str | None = None
    personality: dict[str, JsonValue] = Field(default_factory=dict)
    preferences: dict[str, JsonValue] = Field(default_factory=dict)
    speaking_style: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True
    origin: AudienceOrigin = AudienceOrigin.CUSTOM
    preset_id: str | None = None
    preset_version: int | None = Field(default=None, ge=1)
    revision: int = Field(default=1, ge=1)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "AudienceProfile":
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated_at_ms must not precede created_at_ms")
        if self.origin is AudienceOrigin.PRESET and self.preset_id is None:
            raise ValueError("preset audience profiles require preset_id")
        return self


class AudienceMemory(PersistenceModel):
    memory_id: str = Field(min_length=1)
    audience_id: str = Field(min_length=1)
    memory_type: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=4_000)
    tags: list[MemoryTag] = Field(default_factory=list, max_length=32)
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    origin: MemoryOrigin
    state: MemoryState = MemoryState.ACTIVE
    superseded_by: str | None = None
    last_recalled_at_ms: int | None = Field(default=None, ge=0)
    expires_at_ms: int | None = Field(default=None, ge=0)
    revision: int = Field(default=1, ge=1)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> "AudienceMemory":
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated_at_ms must not precede created_at_ms")
        if self.state is MemoryState.ACTIVE and self.superseded_by is not None:
            raise ValueError("active memories cannot reference a replacement")
        if self.superseded_by == self.memory_id:
            raise ValueError("a memory cannot supersede itself")
        return self


class MemoryEvidence(PersistenceModel):
    memory_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1, max_length=64)
    occurred_at_ms: int = Field(ge=0)
    evidence_summary: str = Field(min_length=1, max_length=500)


class HostRelationship(PersistenceModel):
    audience_id: str = Field(min_length=1)
    summary: str = Field(default="", max_length=2_000)
    state: dict[str, JsonValue] = Field(default_factory=dict)
    source_memory_id: str | None = None
    updated_by: RelationshipUpdatedBy
    revision: int = Field(default=1, ge=1)
    updated_at_ms: int = Field(ge=0)


class PeerRelationship(PersistenceModel):
    audience_id: str = Field(min_length=1)
    peer_audience_id: str = Field(min_length=1)
    summary: str = Field(default="", max_length=2_000)
    state: dict[str, JsonValue] = Field(default_factory=dict)
    source_memory_id: str | None = None
    updated_by: RelationshipUpdatedBy
    revision: int = Field(default=1, ge=1)
    updated_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_distinct_audiences(self) -> "PeerRelationship":
        if self.audience_id == self.peer_audience_id:
            raise ValueError("peer relationships require two distinct audiences")
        return self
