from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from advx_backend.application.audience_service import (
    AudienceService,
    AudienceSessionNotActiveError,
)
from advx_backend.application.builtin_audiences import BUILTIN_AUDIENCES
from advx_backend.contracts.generation import Observation
from advx_backend.domain.audience import (
    AudienceMemory,
    AudienceOrigin,
    AudienceProfile,
    HostRelationship,
    MemoryOrigin,
    PeerRelationship,
    RelationshipUpdatedBy,
)
from advx_backend.domain.session import SessionAudience, SessionRecord
from advx_backend.infrastructure.persistence.sqlite import (
    DatabaseConfig,
    SQLiteDatabase,
    SQLiteUnitOfWorkFactory,
)
from advx_backend.infrastructure.persistence.sqlite.models import SessionAudienceRow


class MutableClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value


class ToggleUnitOfWorkFactory:
    def __init__(self, delegate: SQLiteUnitOfWorkFactory) -> None:
        self.delegate = delegate
        self.available = True
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self.available:
            raise RuntimeError("database unavailable")
        return self.delegate()


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active_database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active_database.start()
    try:
        yield active_database
    finally:
        await active_database.close()


def profile(
    audience_id: str,
    *,
    enabled: bool = True,
    revision: int = 1,
    updated_at_ms: int = 100,
    origin: AudienceOrigin = AudienceOrigin.CUSTOM,
) -> AudienceProfile:
    return AudienceProfile(
        audience_id=audience_id,
        display_name=f"Audience {audience_id}",
        personality={"energy": "calm"},
        preferences={"topics": ["games"]},
        speaking_style={"length": "short"},
        enabled=enabled,
        origin=origin,
        preset_id="test.preset" if origin is AudienceOrigin.PRESET else None,
        preset_version=1 if origin is AudienceOrigin.PRESET else None,
        revision=revision,
        created_at_ms=100,
        updated_at_ms=updated_at_ms,
    )


def memory(
    memory_id: str,
    audience_id: str,
    *,
    importance: float,
) -> AudienceMemory:
    return AudienceMemory(
        memory_id=memory_id,
        audience_id=audience_id,
        memory_type="preference",
        content=f"Memory {memory_id}",
        importance=importance,
        confidence=0.9,
        origin=MemoryOrigin.USER,
        created_at_ms=100,
        updated_at_ms=100,
    )


def observation(session_id: str, observation_id: str = "observation-1") -> Observation:
    return Observation(
        session_id=session_id,
        observation_id=observation_id,
        created_at_ms=1_000,
    )


async def add_session_record(
    unit_of_work_factory: SQLiteUnitOfWorkFactory, session_id: str
) -> None:
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.sessions.add(
            SessionRecord(session_id=session_id, started_at_ms=100, app_version="test")
        )
        await unit_of_work.commit()


@pytest.mark.asyncio
async def test_start_session_filters_disabled_profiles_maps_relationships_and_records_revision(
    database: SQLiteDatabase,
) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    await add_session_record(unit_of_work_factory, "session-1")
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(profile("enabled", revision=7))
        await unit_of_work.audiences.add(profile("peer"))
        await unit_of_work.audiences.add(profile("disabled", enabled=False))
        await unit_of_work.relationships.save_host(
            HostRelationship(
                audience_id="enabled",
                summary="Trusts the host.",
                state={"trust": 0.8},
                updated_by=RelationshipUpdatedBy.USER,
                revision=3,
                updated_at_ms=200,
            ),
            expected_revision=None,
        )
        await unit_of_work.relationships.save_peer(
            PeerRelationship(
                audience_id="enabled",
                peer_audience_id="peer",
                summary="Enjoys co-op games.",
                state={"affinity": 0.6},
                updated_by=RelationshipUpdatedBy.USER,
                revision=4,
                updated_at_ms=210,
            ),
            expected_revision=None,
        )
        await unit_of_work.commit()

    service = AudienceService(unit_of_work_factory=unit_of_work_factory, clock=MutableClock())
    await service.start_session("session-1")

    snapshot = await service.get_snapshot(observation=observation("session-1"))
    contexts = {context.member.audience_id: context for context in snapshot.audiences}

    assert set(contexts) == {"enabled", "peer"}
    assert contexts["enabled"].member.relationships == {
        "host": {
            "summary": "Trusts the host.",
            "state": {"trust": 0.8},
            "source_memory_id": None,
            "updated_by": "user",
            "revision": 1,
            "updated_at_ms": 200,
        },
        "peers": {
            "peer": {
                "audience_id": "peer",
                "summary": "Enjoys co-op games.",
                "state": {"affinity": 0.6},
                "source_memory_id": None,
                "updated_by": "user",
                "revision": 1,
                "updated_at_ms": 210,
            }
        },
    }

    async with database.session_factory() as session:
        rows = list(
            await session.scalars(
                select(SessionAudienceRow).where(SessionAudienceRow.session_id == "session-1")
            )
        )
    revisions = {row.audience_id: row.profile_revision for row in rows}
    assert revisions == {"enabled": 7, "peer": 1}


@pytest.mark.asyncio
async def test_memories_are_isolated_per_audience_and_bounded(database: SQLiteDatabase) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    await add_session_record(unit_of_work_factory, "session-1")
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(profile("a"))
        await unit_of_work.audiences.add(profile("b"))
        await unit_of_work.memories.add(memory("a-high", "a", importance=0.9))
        await unit_of_work.memories.add(memory("a-medium", "a", importance=0.8))
        await unit_of_work.memories.add(memory("a-low", "a", importance=0.7))
        await unit_of_work.memories.add(memory("b-high", "b", importance=1.0))
        await unit_of_work.commit()

    service = AudienceService(
        unit_of_work_factory=unit_of_work_factory,
        clock=MutableClock(),
        max_memories_per_audience=2,
    )
    await service.start_session("session-1")

    snapshot = await service.get_snapshot(observation=observation("session-1"))
    memories_by_audience = {
        context.member.audience_id: context.memories for context in snapshot.audiences
    }

    assert [item.memory_id for item in memories_by_audience["a"]] == ["a-high", "a-medium"]
    assert [item.audience_id for item in memories_by_audience["a"]] == ["a", "a"]
    assert [item.memory_id for item in memories_by_audience["b"]] == ["b-high"]
    assert [item.audience_id for item in memories_by_audience["b"]] == ["b"]


@pytest.mark.asyncio
async def test_loaded_snapshot_survives_database_outage_and_old_session_cannot_read_new_cache(
    database: SQLiteDatabase,
) -> None:
    delegate = SQLiteUnitOfWorkFactory(database.session_factory)
    factory = ToggleUnitOfWorkFactory(delegate)
    await add_session_record(delegate, "session-old")
    await add_session_record(delegate, "session-new")
    async with delegate() as unit_of_work:
        await unit_of_work.audiences.add(profile("a", updated_at_ms=100))
        await unit_of_work.commit()

    service = AudienceService(unit_of_work_factory=factory, clock=MutableClock())
    await service.start_session("session-old")
    factory.available = False
    calls_before_snapshot = factory.calls

    old_snapshot = await service.get_snapshot(observation=observation("session-old"))

    assert [context.member.audience_id for context in old_snapshot.audiences] == ["a"]
    assert factory.calls == calls_before_snapshot
    old_snapshot.audiences[0].member.display_name = "Mutated by caller"
    cached_again = await service.get_snapshot(
        observation=observation("session-old", "same-session-next-observation")
    )
    assert cached_again.audiences[0].member.display_name == "Audience a"

    await service.stop_session("session-old")
    factory.available = True
    await service.start_session("session-new")

    with pytest.raises(AudienceSessionNotActiveError):
        await service.get_snapshot(observation=observation("session-old", "old-late"))
    new_snapshot = await service.get_snapshot(observation=observation("session-new", "new-current"))
    assert new_snapshot.session_id == "session-new"
    assert new_snapshot.observation_id == "new-current"


@pytest.mark.asyncio
async def test_failed_session_audience_write_rolls_back_and_does_not_publish_cache(
    database: SQLiteDatabase,
) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    await add_session_record(unit_of_work_factory, "session-rollback")
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(profile("a"))
        await unit_of_work.audiences.add(profile("b"))
        await unit_of_work.sessions.add_audience(
            SessionAudience(
                session_id="session-rollback",
                audience_id="b",
                profile_revision=1,
                joined_at_ms=100,
            )
        )
        await unit_of_work.commit()

    service = AudienceService(unit_of_work_factory=unit_of_work_factory, clock=MutableClock())

    with pytest.raises(IntegrityError):
        await service.start_session("session-rollback")

    async with database.session_factory() as session:
        rows = list(
            await session.scalars(
                select(SessionAudienceRow).where(
                    SessionAudienceRow.session_id == "session-rollback"
                )
            )
        )
    assert [row.audience_id for row in rows] == ["b"]
    with pytest.raises(AudienceSessionNotActiveError):
        await service.get_snapshot(observation=observation("session-rollback"))


@pytest.mark.asyncio
async def test_builtin_initialization_populates_an_empty_database(
    database: SQLiteDatabase,
) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    service = AudienceService(unit_of_work_factory=unit_of_work_factory, clock=MutableClock())

    created = await service.initialize_builtin_audiences()

    assert {profile.audience_id for profile in created} == {
        template.audience_id for template in BUILTIN_AUDIENCES
    }
    assert all(profile.origin is AudienceOrigin.PRESET for profile in created)


@pytest.mark.asyncio
async def test_builtin_initialization_is_idempotent_and_preserves_existing_profiles(
    database: SQLiteDatabase,
) -> None:
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    existing_id = BUILTIN_AUDIENCES[0].audience_id
    existing = profile(existing_id)
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.audiences.add(existing)
        await unit_of_work.commit()

    service = AudienceService(unit_of_work_factory=unit_of_work_factory, clock=MutableClock())
    created = await service.initialize_builtin_audiences()
    created_again = await service.initialize_builtin_audiences()

    assert {profile.audience_id for profile in created} == {
        template.audience_id for template in BUILTIN_AUDIENCES[1:]
    }
    assert created_again == ()
    async with unit_of_work_factory() as unit_of_work:
        preserved = await unit_of_work.audiences.get(existing_id)
        profiles = await unit_of_work.audiences.list_enabled()
    assert preserved == existing
    assert {profile.audience_id for profile in profiles} == {
        template.audience_id for template in BUILTIN_AUDIENCES
    }
