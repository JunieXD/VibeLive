import os
from dataclasses import dataclass, field

from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.session_service import SessionService
from advx_backend.infrastructure.security.local_token import create_local_token
from advx_backend.infrastructure.system import SystemClock, UuidIdGenerator

LOCAL_TOKEN_ENV = "ADVX_LOCAL_TOKEN"


@dataclass(frozen=True)
class BackendRuntime:
    session_service: SessionService
    realtime_broker: RealtimeBroker
    local_token: str = field(repr=False)

    async def shutdown(self) -> None:
        await self.session_service.shutdown()


def build_runtime(*, local_token: str | None = None) -> BackendRuntime:
    token = create_local_token() if local_token is None else local_token
    if not token:
        raise ValueError("local_token must not be empty")

    broker = RealtimeBroker()
    session_service = SessionService(
        clock=SystemClock(),
        id_generator=UuidIdGenerator(),
        publisher=broker,
    )
    return BackendRuntime(
        session_service=session_service,
        realtime_broker=broker,
        local_token=token,
    )


def build_runtime_from_environment() -> BackendRuntime:
    return build_runtime(local_token=os.environ.get(LOCAL_TOKEN_ENV))
