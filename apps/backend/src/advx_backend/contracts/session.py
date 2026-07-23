from pydantic import BaseModel

from advx_backend.domain.session import SessionState, SessionStatus


class SessionSnapshot(BaseModel):
    session_id: str | None
    state: SessionState
    started_at_ms: int | None
    updated_at_ms: int
    revision: int

    @classmethod
    def from_domain(cls, status: SessionStatus) -> "SessionSnapshot":
        return cls(
            session_id=status.session_id,
            state=status.state,
            started_at_ms=status.started_at_ms,
            updated_at_ms=status.updated_at_ms,
            revision=status.revision,
        )
