from advx_backend.application.ports.persistence import UnitOfWorkFactory
from advx_backend.domain.session import SessionOutcome, SessionRecord


class SQLiteSessionRecordStore:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def record_started(self, record: SessionRecord) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.sessions.add(record)
            await unit_of_work.commit()

    async def record_finished(
        self,
        session_id: str,
        *,
        ended_at_ms: int,
        outcome: SessionOutcome,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.sessions.finish(
                session_id,
                ended_at_ms=ended_at_ms,
                outcome=outcome,
            )
            await unit_of_work.commit()

    async def recover_interrupted(self, *, ended_at_ms: int) -> int:
        async with self._unit_of_work_factory() as unit_of_work:
            recovered = await unit_of_work.sessions.recover_interrupted(ended_at_ms=ended_at_ms)
            await unit_of_work.commit()
            return recovered
