from typing import Protocol

from advx_backend.domain.session import SessionStatus


class Clock(Protocol):
    def now_ms(self) -> int: ...


class IdGenerator(Protocol):
    def new_id(self) -> str: ...


class SessionStatusPublisher(Protocol):
    async def publish_session_status(self, status: SessionStatus) -> None: ...
