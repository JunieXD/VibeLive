from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RoomEventSource(StrEnum):
    USER_TEXT = "user_text"
    USER_VOICE = "user_voice"
    AUDIENCE_BARRAGE = "audience_barrage"
    SCREEN_OBSERVATION = "screen_observation"
    SYSTEM_EVENT = "system_event"


class RoomEvent(BaseModel):
    event_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source_type: RoomEventSource
    source_id: str | None = None
    created_at_ms: int = Field(ge=0)
    text: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
