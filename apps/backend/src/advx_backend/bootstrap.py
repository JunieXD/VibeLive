import os
from dataclasses import dataclass, field
from pathlib import Path

from advx_backend.application.ports.persistence import UnitOfWorkFactory
from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.session_service import SessionService
from advx_backend.infrastructure.persistence.sqlite import (
    DatabaseConfig,
    SQLiteDatabase,
    SQLiteSessionRecordStore,
    SQLiteUnitOfWorkFactory,
)
from advx_backend.infrastructure.security.local_token import create_local_token
from advx_backend.infrastructure.system import SystemClock, UuidIdGenerator

BACKEND_VERSION = "0.1.0"
LOCAL_TOKEN_ENV = "ADVX_LOCAL_TOKEN"
DATA_DIRECTORY_ENV = "ADVX_DATA_DIR"
DEFAULT_DATA_DIRECTORY = Path.cwd() / ".advx-data"


@dataclass
class BackendRuntime:
    session_service: SessionService
    realtime_broker: RealtimeBroker
    database: SQLiteDatabase
    unit_of_work_factory: UnitOfWorkFactory
    session_record_store: SQLiteSessionRecordStore
    clock: SystemClock
    local_token: str = field(repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    async def startup(self) -> None:
        if self._started:
            return
        await self.database.start()
        await self.session_record_store.recover_interrupted(ended_at_ms=self.clock.now_ms())
        self._started = True

    async def shutdown(self) -> None:
        try:
            await self.session_service.shutdown()
        finally:
            await self.database.close()
            self._started = False


def build_runtime(
    *,
    local_token: str | None = None,
    data_directory: str | Path | None = None,
) -> BackendRuntime:
    token = create_local_token() if local_token is None else local_token
    if not token:
        raise ValueError("local_token must not be empty")

    resolved_data_directory = (
        Path(DEFAULT_DATA_DIRECTORY if data_directory is None else data_directory)
        .expanduser()
        .resolve()
    )
    database = SQLiteDatabase(DatabaseConfig(data_directory=resolved_data_directory))
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    session_record_store = SQLiteSessionRecordStore(unit_of_work_factory)
    broker = RealtimeBroker()
    clock = SystemClock()
    session_service = SessionService(
        clock=clock,
        id_generator=UuidIdGenerator(),
        publisher=broker,
        session_records=session_record_store,
        app_version=BACKEND_VERSION,
    )
    return BackendRuntime(
        session_service=session_service,
        realtime_broker=broker,
        database=database,
        unit_of_work_factory=unit_of_work_factory,
        session_record_store=session_record_store,
        clock=clock,
        local_token=token,
    )


def build_runtime_from_environment() -> BackendRuntime:
    return build_runtime(
        local_token=os.environ.get(LOCAL_TOKEN_ENV),
        data_directory=os.environ.get(DATA_DIRECTORY_ENV),
    )
