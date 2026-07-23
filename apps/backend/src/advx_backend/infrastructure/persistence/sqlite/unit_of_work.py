from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from advx_backend.infrastructure.persistence.sqlite.repositories import (
    SQLiteAudienceRepository,
    SQLiteMemoryRepository,
    SQLiteRelationshipRepository,
    SQLiteSessionRecordRepository,
)


class SQLiteUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self.audiences = SQLiteAudienceRepository(self._session)
        self.relationships = SQLiteRelationshipRepository(self._session)
        self.memories = SQLiteMemoryRepository(self._session)
        self.sessions = SQLiteSessionRecordRepository(self._session)
        self._committed = False

    async def __aenter__(self) -> "SQLiteUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                await self.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self._session.rollback()


@dataclass(frozen=True)
class SQLiteUnitOfWorkFactory:
    session_factory: async_sessionmaker[AsyncSession]

    def __call__(self) -> SQLiteUnitOfWork:
        return SQLiteUnitOfWork(self.session_factory)
