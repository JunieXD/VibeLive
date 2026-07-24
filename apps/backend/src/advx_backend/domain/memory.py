from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoomMemoryType(StrEnum):
    USER_PREFERENCE = "user_preference"
    REAL_WORLD_FACT = "real_world_fact"
    ROOM_LORE = "room_lore"
    SHARED_EXPERIENCE = "shared_experience"


class RoomWorkingMemory(MemoryDomainModel):
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    event_ids: list[str] = Field(default_factory=list, max_length=512)
    updated_at_ms: int = Field(ge=0)


class RoomLongTermMemory(MemoryDomainModel):
    memory_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    memory_type: RoomMemoryType
    content: str = Field(min_length=1, max_length=4_000)
    evidence_event_ids: list[str] = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)
    revision: int = Field(ge=1)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    revoked_at_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "RoomLongTermMemory":
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated_at_ms must not precede created_at_ms")
        if self.revoked_at_ms is not None and self.revoked_at_ms < self.created_at_ms:
            raise ValueError("revoked_at_ms must not precede created_at_ms")
        return self


class RoomMemorySlice(MemoryDomainModel):
    room_id: str = Field(min_length=1, max_length=128)
    memory_revision: int = Field(ge=0)
    memory_ids: list[str] = Field(default_factory=list, max_length=128)
    items: list[RoomLongTermMemory] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def validate_items(self) -> "RoomMemorySlice":
        if self.items and self.memory_ids != [item.memory_id for item in self.items]:
            raise ValueError("memory_ids must match the selected memory items")
        if any(item.room_id != self.room_id for item in self.items):
            raise ValueError("memory slice items must belong to the same room")
        return self
