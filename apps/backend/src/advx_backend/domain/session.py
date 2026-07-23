from dataclasses import dataclass
from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class SessionStatus:
    session_id: str | None
    state: SessionState
    started_at_ms: int | None
    updated_at_ms: int
    revision: int


def can_stop_session(state: SessionState) -> bool:
    return state not in {SessionState.IDLE, SessionState.STOPPING}
