from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import update

from advx_backend.application.meme_service import ModeMemeService
from advx_backend.application.memory_service import RoomMemoryService
from advx_backend.application.ports.memory import RoomMemoryCandidate
from advx_backend.domain.meme import MemeCandidate, ModeMemeState
from advx_backend.domain.memory import RoomMemoryType
from advx_backend.infrastructure.persistence.sqlite import DatabaseConfig, SQLiteDatabase
from advx_backend.infrastructure.persistence.sqlite.memory_meme_adapters import (
    SQLiteModeMemeServiceRepository,
    SQLiteRoomEventReader,
    SQLiteRoomMemoryServiceRepository,
)
from advx_backend.infrastructure.persistence.sqlite.models import (
    RoomLongTermMemoryRow,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    PersistedRoomEvent,
    RuntimePersistenceConflictError,
    SQLiteRoomEventRepository,
    SQLiteRoomRepository,
    SQLiteSessionRuntimeRepository,
)

DAY_MS = 24 * 60 * 60 * 1_000


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active.start()
    try:
        yield active
    finally:
        await active.close()


class Clock:
    def __init__(self, now_ms: int) -> None:
        self.value = now_ms

    def now_ms(self) -> int:
        return self.value


class Fence:
    async def accepts(self, **scope: object) -> bool:
        return scope["audience_epoch"] == 1


async def seed_room(session: object) -> None:
    rooms = SQLiteRoomRepository(session)
    await rooms.get_or_create("room-1", display_name="Room", now_ms=100)
    runtime = SQLiteSessionRuntimeRepository(session)
    await runtime.start(
        session_id="session-1",
        room_id="room-1",
        client_request_id="request-1",
        request_hash="hash-1",
        apply_id="apply-1",
        canonical_spec_json="{}",
        diff_summary_json="{}",
        app_version="test",
        now_ms=110,
    )


@pytest.mark.asyncio
async def test_memory_adapter_maps_evidence_and_preserves_idempotent_cas(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        await seed_room(session)
        await SQLiteRoomEventRepository(session).append(
            PersistedRoomEvent(
                event_id="event-1",
                room_id="room-1",
                session_id="session-1",
                sequence=1,
                source_type="user_text",
                source_id="message-1",
                audience_epoch=1,
                content_json='{"text":"likes tactics"}',
                content_hash="hash",
                occurred_at_ms=120,
            )
        )
        repository = SQLiteRoomMemoryServiceRepository(session)
        service = RoomMemoryService(
            repository=repository,
            event_reader=SQLiteRoomEventReader(session),
            session_fence=Fence(),
            clock=Clock(200),
        )
        candidate = RoomMemoryCandidate(
            candidate_id="candidate-1",
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            idempotency_key="memory-request-1",
            base_revision=0,
            memory_id="memory-1",
            memory_type=RoomMemoryType.USER_PREFERENCE,
            content="The user likes tactics.",
            evidence_event_ids=("event-1",),
        )

        created = await service.commit_candidate(candidate)
        duplicate = await service.commit_candidate(candidate)
        memory_slice = await service.read_slice(
            room_id="room-1",
            event_ids=("event-1",),
            limit=8,
        )
        active = await service.list_active("room-1")

        assert created.created is True
        assert duplicate.created is False
        assert duplicate.memory_revision == 1
        assert duplicate.head_revision == 1
        assert memory_slice.memory_ids == ["memory-1"]
        assert memory_slice.items[0].content == "The user likes tactics."
        assert memory_slice.items[0].memory_type is RoomMemoryType.USER_PREFERENCE
        assert memory_slice.items[0].evidence_event_ids == ["event-1"]
        assert active[0].evidence_event_ids == ["event-1"]

        with pytest.raises(RuntimePersistenceConflictError):
            await service.commit_candidate(
                RoomMemoryCandidate(
                    **{
                        **candidate.__dict__,
                        "candidate_id": "candidate-2",
                        "idempotency_key": "memory-request-2",
                        "base_revision": 0,
                    }
                )
            )

        revoked = await service.revoke("room-1", "memory-1", expected_revision=1)
        assert revoked.revoked_at_ms == 200
        assert (
            await service.delete("room-1", "memory-1", expected_revision=2)
            is True
        )
        await session.commit()


@pytest.mark.asyncio
async def test_memory_slice_and_manual_mutations_are_scoped_and_cas_guarded(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        await seed_room(session)
        events = SQLiteRoomEventRepository(session)
        for sequence in (1, 2):
            await events.append(
                PersistedRoomEvent(
                    event_id=f"event-{sequence}",
                    room_id="room-1",
                    session_id="session-1",
                    sequence=sequence,
                    source_type="user_text",
                    source_id=f"message-{sequence}",
                    audience_epoch=1,
                    content_json=f'{{"text":"evidence {sequence}"}}',
                    content_hash=f"hash-{sequence}",
                    occurred_at_ms=120 + sequence,
                )
            )
        service = RoomMemoryService(
            repository=SQLiteRoomMemoryServiceRepository(session),
            event_reader=SQLiteRoomEventReader(session),
            session_fence=Fence(),
            clock=Clock(200),
        )
        for index in (1, 2):
            result = await service.commit_candidate(
                RoomMemoryCandidate(
                    candidate_id=f"candidate-{index}",
                    room_id="room-1",
                    session_id="session-1",
                    audience_epoch=1,
                    idempotency_key=f"request-{index}",
                    base_revision=index - 1,
                    memory_id=f"memory-{index}",
                    memory_type=RoomMemoryType.USER_PREFERENCE,
                    content=f"memory {index}",
                    evidence_event_ids=(f"event-{index}",),
                )
            )
            assert result.accepted
            assert result.memory_revision == 1
            assert result.head_revision == index

        repository = SQLiteRoomMemoryServiceRepository(session)
        event_one = await repository.read_slice(
            room_id="room-1",
            event_ids=("event-1",),
            limit=8,
        )
        assert event_one.memory_ids == ["memory-1"]

        edited = await repository.edit(
            "room-1",
            "memory-1",
            expected_revision=1,
            content="edited",
            confidence=0.9,
            evidence_event_ids=("event-1",),
            now_ms=300,
        )
        merged = await repository.merge(
            "room-1",
            "memory-1",
            "memory-2",
            expected_revision=edited.revision,
            source_expected_revision=1,
            content="merged",
            now_ms=400,
        )
        replacement = await repository.replace(
            "room-1",
            "memory-1",
            expected_revision=merged.revision,
            replacement_memory_id="memory-3",
            content="replacement",
            evidence_event_ids=("event-1", "event-2"),
            now_ms=500,
        )
        assert replacement.evidence_event_ids == ["event-1", "event-2"]
        assert [item.memory_id for item in await repository.list_active("room-1")] == [
            "memory-3"
        ]

        await session.execute(
            update(RoomLongTermMemoryRow)
            .where(RoomLongTermMemoryRow.memory_id == "memory-3")
            .values(expires_at_ms=1)
        )
        assert (
            await repository.read_slice(
                room_id="room-1",
                event_ids=("event-1",),
                limit=8,
            )
        ).items == []


def meme_candidate(candidate_id: str, *, namespace: str = "mode-a") -> MemeCandidate:
    return MemeCandidate(
        candidate_id=candidate_id,
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="wave-1",
        namespace_id=namespace,
        text=candidate_id,
        evidence_event_ids=["event-1"],
        created_at_ms=1,
    )


@pytest.mark.asyncio
async def test_meme_adapter_isolates_namespaces_and_persists_lifecycle(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        await seed_room(session)
        repository = SQLiteModeMemeServiceRepository(session)
        service = ModeMemeService(
            repository=repository,
            session_fence=Fence(),
            clock=Clock(40 * DAY_MS),
        )
        plain = meme_candidate("plain")
        pinned = meme_candidate("pinned")
        used = meme_candidate("used")
        other = meme_candidate("other", namespace="mode-b")

        for item in (plain, pinned, used, other):
            result = await service.commit_candidate(item)
            assert result.accepted is True

        repository = SQLiteModeMemeServiceRepository(session)
        service = ModeMemeService(
            repository=repository,
            session_fence=Fence(),
            clock=Clock(40 * DAY_MS),
        )
        pinned_meme = await service.pin("meme:pinned", expected_revision=1)
        used_meme = await repository.record_use(
            "meme:used",
            expected_revision=1,
            now_ms=2,
        )
        used_meme = await repository.record_use(
            "meme:used",
            expected_revision=used_meme.revision,
            now_ms=2,
        )
        await repository.record_use(
            "meme:used",
            expected_revision=used_meme.revision,
            now_ms=2,
        )
        archived = await service.auto_archive("mode-a")
        active_a = await service.list_active("mode-a")
        active_b = await service.list_active("mode-b")

        assert pinned_meme.pinned is True
        assert archived == ("meme:plain",)
        assert {item.meme_id for item in active_a} == {"meme:pinned", "meme:used"}
        assert [item.meme_id for item in active_b] == ["meme:other"]

        disabled = await service.disable("meme:pinned", expected_revision=2)
        restored = await service.restore(
            "meme:pinned",
            expected_revision=disabled.revision,
        )
        undone = await service.undo(
            "meme:pinned",
            expected_revision=restored.revision,
        )
        assert undone.state is ModeMemeState.REVOKED
        await session.commit()


@pytest.mark.asyncio
async def test_meme_adapter_holds_pending_candidates_without_creating_memes(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        await seed_room(session)
        repository = SQLiteModeMemeServiceRepository(session)
        service = ModeMemeService(
            repository=repository,
            session_fence=Fence(),
            clock=Clock(100),
        )
        service.set_auto_ingest("mode-a", enabled=False)
        item = meme_candidate("pending")

        result = await service.commit_candidate(item)

        assert result.pending is True
        assert await repository.list_pending("mode-a") == (item,)
        assert await service.list_active("mode-a") == ()

        approved = await repository.approve_candidate(
            "mode-a",
            item.candidate_id,
            now_ms=101,
        )
        assert approved.accepted is True
        edited = await repository.edit(
            approved.meme_id or "",
            expected_revision=1,
            text="edited pending meme",
            intensity=0.8,
            now_ms=102,
        )
        assert edited.text == "edited pending meme"
        assert edited.intensity == 0.8

        rejected_candidate = meme_candidate("rejected")
        await repository.save_candidate(rejected_candidate)
        rejected = await repository.reject_candidate(
            "mode-a",
            rejected_candidate.candidate_id,
            now_ms=103,
        )
        assert rejected.outcome.value == "rejected"

        with pytest.raises(RuntimePersistenceConflictError):
            await repository.save_candidate(
                meme_candidate("duplicate-key").model_copy(
                    update={"idempotency_key": "rejected"}
                )
            )
