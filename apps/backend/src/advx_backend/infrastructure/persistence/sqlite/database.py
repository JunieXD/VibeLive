import asyncio
import os
import sqlite3
import time
from contextlib import closing
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
    migration_backup_limit: int = 5
    migration_backup_retention_ms: int = 14 * 24 * 60 * 60 * 1_000

    def __post_init__(self) -> None:
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("filename must be a non-empty basename")
        if self.busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        if self.migration_backup_limit < 1:
            raise ValueError("migration_backup_limit must be positive")
        if self.migration_backup_retention_ms < 1:
            raise ValueError("migration_backup_retention_ms must be positive")

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
        self._startup_error: dict[str, str] | None = None

    @property
    def path(self) -> Path:
        return self.config.path

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    @property
    def started(self) -> bool:
        return self._started

    @property
    def startup_error(self) -> dict[str, str] | None:
        return None if self._startup_error is None else dict(self._startup_error)

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            self._prepare_data_directory()
            backup_path: Path | None = None
            stage = "validation"
            try:
                await asyncio.to_thread(self._validate_existing_database)
                stage = "backup"
                backup_path = await asyncio.to_thread(self._create_migration_backup)
                stage = "migration"
                await asyncio.to_thread(self._upgrade_schema)
                stage = "post_migration_validation"
                await asyncio.to_thread(self._validate_database_file, self.path)
                stage = "connection"
                self._restrict_database_permissions()
                async with self._engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    await connection.execute(text("BEGIN IMMEDIATE"))
                    await connection.rollback()
            except Exception as error:
                self._startup_error = {
                    "code": f"sqlite_{stage}_failed",
                    "message": str(error),
                    "backup_path": "" if backup_path is None else str(backup_path),
                }
                self._started = False
                raise
            self._started = True
            self._startup_error = None

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._engine.dispose()
            self._started = False

    async def mark_startup_failed(self, *, code: str, error: Exception) -> None:
        async with self._lifecycle_lock:
            await self._engine.dispose()
            self._started = False
            self._startup_error = {
                "code": code,
                "message": str(error),
                "backup_path": "",
            }

    async def restore_migration_backup(self) -> Path:
        async with self._lifecycle_lock:
            if self._started:
                raise RuntimeError(
                    "cannot restore a migration backup while database is started"
                )
            if self._startup_error is None:
                raise RuntimeError("no failed migration backup is available")
            backup_path = Path(self._startup_error.get("backup_path", ""))
            if not backup_path.is_file():
                raise RuntimeError("failed migration backup is unavailable")
            await self._engine.dispose()
            await asyncio.to_thread(self._restore_backup, backup_path)
            self._restrict_database_permissions()
            return backup_path

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

    def _create_migration_backup(self) -> Path | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        backup_directory = self.config.data_directory / "migration-backups"
        backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        timestamp = int(time.time() * 1_000)
        backup_path = backup_directory / f"{self.path.name}.{timestamp}.bak"
        with (
            closing(sqlite3.connect(self.path)) as source,
            closing(sqlite3.connect(backup_path)) as destination,
        ):
            source.backup(destination)
        self._validate_database_file(backup_path)
        try:
            os.chmod(backup_path, 0o600)
        except OSError:
            pass
        self._prune_migration_backups(backup_directory, now_ms=timestamp)
        return backup_path

    def _prune_migration_backups(self, backup_directory: Path, *, now_ms: int) -> None:
        backups = sorted(
            backup_directory.glob(f"{self.path.name}.*.bak"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        cutoff_ms = now_ms - self.config.migration_backup_retention_ms
        for index, path in enumerate(backups):
            timestamp_text = path.name.removesuffix(".bak").rsplit(".", 1)[-1]
            try:
                timestamp_ms = int(timestamp_text)
            except ValueError:
                timestamp_ms = 0
            if index >= self.config.migration_backup_limit or timestamp_ms < cutoff_ms:
                path.unlink(missing_ok=True)

    def _restore_backup(self, backup_path: Path) -> None:
        self._validate_database_file(backup_path)
        with (
            closing(sqlite3.connect(backup_path)) as source,
            closing(sqlite3.connect(self.path)) as destination,
        ):
            source.backup(destination)
        self._validate_database_file(self.path)

    def _validate_existing_database(self) -> None:
        if self.path.exists() and self.path.stat().st_size > 0:
            self._validate_database_file(self.path)

    @staticmethod
    def _validate_database_file(path: Path) -> None:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            detail = "unknown" if result is None else str(result[0])
            raise sqlite3.DatabaseError(f"SQLite quick_check failed: {detail}")
