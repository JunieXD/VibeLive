from typing import Any

from pydantic import BaseModel, Field


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
