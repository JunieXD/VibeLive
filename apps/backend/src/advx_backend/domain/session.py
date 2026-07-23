from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


class SessionOutcome(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class SessionStatus:
    session_id: str | None
    state: SessionState
    started_at_ms: int | None
    updated_at_ms: int
    revision: int


class SessionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str = Field(min_length=1)
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int | None = Field(default=None, ge=0)
    outcome: SessionOutcome | None = None
    app_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_completion(self) -> "SessionRecord":
        if (self.ended_at_ms is None) != (self.outcome is None):
            raise ValueError("ended_at_ms and outcome must be set together")
        if self.ended_at_ms is not None and self.ended_at_ms < self.started_at_ms:
            raise ValueError("ended_at_ms must not precede started_at_ms")
        return self


class SessionAudience(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str = Field(min_length=1)
    audience_id: str = Field(min_length=1)
    profile_revision: int = Field(ge=1)
    joined_at_ms: int = Field(ge=0)
    left_at_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_presence(self) -> "SessionAudience":
        if self.left_at_ms is not None and self.left_at_ms < self.joined_at_ms:
            raise ValueError("left_at_ms must not precede joined_at_ms")
        return self


def can_stop_session(state: SessionState) -> bool:
    return state not in {SessionState.IDLE, SessionState.STOPPING}
