from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from advx_backend.application.ports.persistence import RevisionConflictError
from advx_backend.bootstrap import build_runtime
from advx_backend.domain.audience import (
    AudienceMemory,
    AudienceProfile,
    HostRelationship,
    MemoryEvidence,
    MemoryOrigin,
    PeerRelationship,
    RelationshipUpdatedBy,
)
from advx_backend.domain.session import (
    SessionAudience,
    SessionOutcome,
    SessionRecord,
)
from advx_backend.infrastructure.persistence.sqlite import (
    DatabaseConfig,
    SQLiteDatabase,
    SQLiteUnitOfWorkFactory,
)
from advx_backend.infrastructure.persistence.sqlite.models import (
    AudienceMemoryRow,
    Base,
    MemoryEvidenceRow,
    SessionAudienceRow,
)


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active_database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active_database.start()
    try:
        yield active_database
    finally:
        await active_database.close()


def profile(audience_id: str, *, now_ms: int = 100) -> AudienceProfile:
    return AudienceProfile(
        audience_id=audience_id,
        display_name=f"Audience {audience_id}",
        personality={"energy": "calm"},
        preferences={"topics": ["games"]},
        speaking_style={"length": "short"},
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )


def memory(
    memory_id: str,
    audience_id: str,
    *,
    origin: MemoryOrigin = MemoryOrigin.USER,
    now_ms: int = 200,
) -> AudienceMemory:
    return AudienceMemory(
        memory_id=memory_id,
        audience_id=audience_id,
        memory_type="preference",
        content=f"Content for {memory_id}",
        tags=["game"],
        importance=0.8,
        confidence=0.9,
        origin=origin,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )


def evidence(
    memory_id: str,
    session_id: str,
    source_event_id: str = "event-1",
) -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory_id,
        session_id=session_id,
        source_event_id=source_event_id,
        source_type="user_text",
        occurred_at_ms=210,
        evidence_summary="User mentioned a game preference.",
    )


@pytest.mark.asyncio
async def test_database_migrates_and_enables_required_pragmas(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        foreign_keys = await session.scalar(text("PRAGMA foreign_keys"))
        journal_mode = await session.scalar(text("PRAGMA journal_mode"))
        busy_timeout = await session.scalar(text("PRAGMA busy_timeout"))
        migration = await session.scalar(text("SELECT version_num FROM alembic_version"))

    assert foreign_keys == 1
    assert journal_mode == "wal"
    assert busy_timeout == 5_000
    assert migration == "0001_initial"


@pytest.mark.asyncio
async def test_migration_matches_sqlalchemy_metadata(database: SQLiteDatabase) -> None:
    def compare_schema(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection)
        return compare_metadata(context, Base.metadata)

    async with database.session_factory() as session:
        connection = await session.connection()
        differences = await connection.run_sync(compare_schema)

    assert differences == []


@pytest.mark.asyncio
async def test_foreign_keys_and_audience_cascades(
    database: SQLiteDatabase,
) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)

    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.memories.add(memory("orphan", "missing"))
            await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(profile("a"))
        await unit_of_work.audiences.add(profile("b"))
        await unit_of_work.sessions.add(
            SessionRecord(session_id="session-1", started_at_ms=150, app_version="test")
        )
        await unit_of_work.memories.add(
            memory("memory-a", "a", origin=MemoryOrigin.EXTRACTED),
            [evidence("memory-a", "session-1")],
        )
        await unit_of_work.sessions.add_audience(
            SessionAudience(
                session_id="session-1",
                audience_id="a",
                profile_revision=1,
                joined_at_ms=160,
            )
        )
        await unit_of_work.relationships.save_host(
            HostRelationship(
                audience_id="a",
                summary="Trusts the host.",
                state={"trust": 0.7},
                source_memory_id="memory-a",
                updated_by=RelationshipUpdatedBy.MEMORY,
                updated_at_ms=220,
            ),
            expected_revision=None,
        )
        await unit_of_work.relationships.save_peer(
            PeerRelationship(
                audience_id="a",
                peer_audience_id="b",
                summary="Often agrees.",
                state={"affinity": 0.5},
                source_memory_id="memory-a",
                updated_by=RelationshipUpdatedBy.MEMORY,
                updated_at_ms=220,
            ),
            expected_revision=None,
        )
        await unit_of_work.commit()

    async with database.session_factory() as session:
        stored_json = await session.scalar(
            text("SELECT personality_json FROM audience_profiles WHERE audience_id = 'a'")
        )
    assert stored_json is not None
    assert '"schema_version":1' in stored_json

    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.audiences.delete("a") is True
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.memories.get("a", "memory-a") is None
        assert await unit_of_work.memories.evidence_for("a", "memory-a") == []
        assert await unit_of_work.relationships.get_host("a") is None
        assert await unit_of_work.relationships.get_peer("a", "b") is None

    async with database.session_factory() as session:
        participation_count = await session.scalar(
            select(func.count()).select_from(SessionAudienceRow)
        )
    assert participation_count == 0


@pytest.mark.asyncio
async def test_profile_and_memory_updates_reject_stale_revisions(
    database: SQLiteDatabase,
) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    original_profile = profile("a")
    original_memory = memory("memory-a", "a", origin=MemoryOrigin.EXTRACTED)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(original_profile)
        await unit_of_work.sessions.add(
            SessionRecord(session_id="session-1", started_at_ms=150, app_version="test")
        )
        await unit_of_work.memories.add(
            original_memory,
            [evidence("memory-a", "session-1")],
        )
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        updated_profile = await unit_of_work.audiences.update(
            original_profile.model_copy(update={"display_name": "Updated", "updated_at_ms": 201}),
            expected_revision=1,
        )
        updated_memory = await unit_of_work.memories.update(
            original_memory.model_copy(update={"content": "Updated memory", "updated_at_ms": 201}),
            expected_revision=1,
        )
        await unit_of_work.commit()

    assert updated_profile.revision == 2
    assert updated_memory.revision == 2

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.relationships.save_host(
            HostRelationship(
                audience_id="a",
                summary="Must survive a stale edit.",
                source_memory_id="memory-a",
                updated_by=RelationshipUpdatedBy.MEMORY,
                updated_at_ms=202,
            ),
            expected_revision=None,
        )
        await unit_of_work.commit()

    with pytest.raises(RevisionConflictError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.audiences.update(
                original_profile.model_copy(update={"display_name": "Stale", "updated_at_ms": 202}),
                expected_revision=1,
            )

    with pytest.raises(RevisionConflictError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.memories.update(
                original_memory.model_copy(
                    update={"content": "Stale memory", "updated_at_ms": 202}
                ),
                expected_revision=1,
            )

    async with unit_of_work_factory() as unit_of_work:
        stored_profile = await unit_of_work.audiences.get("a")
        stored_memory = await unit_of_work.memories.get("a", "memory-a")
        stored_relationship = await unit_of_work.relationships.get_host("a")
    assert stored_profile is not None
    assert stored_profile.display_name == "Updated"
    assert stored_profile.revision == 2
    assert stored_memory is not None
    assert stored_memory.content == "Updated memory"
    assert stored_memory.revision == 2
    assert stored_relationship is not None
    assert stored_relationship.source_memory_id == "memory-a"


@pytest.mark.asyncio
async def test_memories_are_isolated_by_audience_and_physically_deleted(
    database: SQLiteDatabase,
) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(profile("a"))
        await unit_of_work.audiences.add(profile("b"))
        await unit_of_work.sessions.add(
            SessionRecord(session_id="session-1", started_at_ms=150, app_version="test")
        )
        await unit_of_work.memories.add(
            memory("memory-a", "a", origin=MemoryOrigin.EXTRACTED),
            [evidence("memory-a", "session-1")],
        )
        await unit_of_work.memories.add(memory("memory-b", "b"))
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.memories.get("b", "memory-a") is None
        assert await unit_of_work.memories.evidence_for("b", "memory-a") == []
        assert await unit_of_work.memories.delete("b", "memory-a") is False
        memories_a = await unit_of_work.memories.list_active("a", now_ms=300)
        memories_b = await unit_of_work.memories.list_active("b", now_ms=300)
    assert [item.memory_id for item in memories_a] == ["memory-a"]
    assert [item.memory_id for item in memories_b] == ["memory-b"]

    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.memories.delete("a", "memory-a") is True
        await unit_of_work.commit()

    async with database.session_factory() as session:
        memory_count = await session.scalar(
            select(func.count())
            .select_from(AudienceMemoryRow)
            .where(AudienceMemoryRow.memory_id == "memory-a")
        )
        evidence_count = await session.scalar(
            select(func.count())
            .select_from(MemoryEvidenceRow)
            .where(MemoryEvidenceRow.memory_id == "memory-a")
        )
    assert memory_count == 0
    assert evidence_count == 0


@pytest.mark.asyncio
async def test_memory_and_evidence_write_is_atomic(database: SQLiteDatabase) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(profile("a"))
        await unit_of_work.sessions.add(
            SessionRecord(session_id="session-1", started_at_ms=150, app_version="test")
        )
        await unit_of_work.commit()

    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.memories.add(
                memory("rolled-back", "a", origin=MemoryOrigin.EXTRACTED),
                [evidence("rolled-back", "missing-session")],
            )
            await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.memories.get("a", "rolled-back") is None

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.memories.add(
            memory("committed", "a", origin=MemoryOrigin.EXTRACTED),
            [
                evidence("committed", "session-1", "event-1"),
                evidence("committed", "session-1", "event-2"),
            ],
        )
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        stored = await unit_of_work.memories.get("a", "committed")
        stored_evidence = await unit_of_work.memories.evidence_for("a", "committed")
    assert stored is not None
    assert [item.source_event_id for item in stored_evidence] == ["event-1", "event-2"]


@pytest.mark.asyncio
async def test_runtime_recovers_interrupted_sessions(tmp_path: Path) -> None:
    database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await database.start()
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.sessions.add(
            SessionRecord(session_id="open", started_at_ms=100, app_version="test")
        )
        await unit_of_work.sessions.add(
            SessionRecord(
                session_id="future-clock",
                started_at_ms=9_999_999_999_999,
                app_version="test",
            )
        )
        await unit_of_work.sessions.add(
            SessionRecord(
                session_id="finished",
                started_at_ms=100,
                ended_at_ms=200,
                outcome=SessionOutcome.COMPLETED,
                app_version="test",
            )
        )
        await unit_of_work.commit()
    await database.close()

    runtime = build_runtime(local_token="test-token", data_directory=tmp_path)
    await runtime.startup()
    try:
        async with runtime.unit_of_work_factory() as unit_of_work:
            recovered = await unit_of_work.sessions.get("open")
            recovered_future = await unit_of_work.sessions.get("future-clock")
            finished = await unit_of_work.sessions.get("finished")
    finally:
        await runtime.shutdown()

    assert recovered is not None
    assert recovered.outcome is SessionOutcome.INTERRUPTED
    assert recovered.ended_at_ms is not None
    assert recovered_future is not None
    assert recovered_future.outcome is SessionOutcome.INTERRUPTED
    assert recovered_future.ended_at_ms == recovered_future.started_at_ms
    assert finished is not None
    assert finished.outcome is SessionOutcome.COMPLETED
    assert finished.ended_at_ms == 200
