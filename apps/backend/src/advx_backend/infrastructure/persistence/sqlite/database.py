import asyncio
import os
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from advx_backend.infrastructure.persistence.sqlite.pragmas import (
    configure_sqlite_connection,
)


@dataclass(frozen=True)
class DatabaseConfig:
    data_directory: Path
    filename: str = "advx.sqlite3"
    busy_timeout_ms: int = 5_000

    def __post_init__(self) -> None:
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("filename must be a non-empty basename")
        if self.busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")

    @property
    def path(self) -> Path:
        return self.data_directory / self.filename


class SQLiteDatabase:
    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        async_url = URL.create(
            drivername="sqlite+aiosqlite",
            database=str(config.path),
        )
        self._engine: AsyncEngine = create_async_engine(async_url)
        event.listen(
            self._engine.sync_engine,
            "connect",
            partial(
                configure_sqlite_connection,
                busy_timeout_ms=config.busy_timeout_ms,
            ),
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self._lifecycle_lock = asyncio.Lock()
        self._started = False

    @property
    def path(self) -> Path:
        return self.config.path

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            self._prepare_data_directory()
            await asyncio.to_thread(self._upgrade_schema)
            self._restrict_database_permissions()
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            self._started = True

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._engine.dispose()
            self._started = False

    def _prepare_data_directory(self) -> None:
        self.config.data_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.config.data_directory, 0o700)
        except OSError:
            pass

    def _restrict_database_permissions(self) -> None:
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _upgrade_schema(self) -> None:
        migration_config = Config()
        script_location = Path(__file__).with_name("migrations")
        sync_url = URL.create(drivername="sqlite", database=str(self.path))
        migration_config.set_main_option(
            "script_location",
            str(script_location).replace("%", "%%"),
        )
        migration_config.set_main_option(
            "sqlalchemy.url",
            sync_url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        migration_config.attributes["busy_timeout_ms"] = self.config.busy_timeout_ms
        command.upgrade(migration_config, "head")
