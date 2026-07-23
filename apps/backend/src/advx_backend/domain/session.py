from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


def can_stop_session(state: SessionState) -> bool:
    return state not in {SessionState.IDLE, SessionState.STOPPING}
