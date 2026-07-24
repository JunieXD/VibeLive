from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from advx_backend.infrastructure.persistence.sqlite import DatabaseConfig, SQLiteDatabase
from advx_backend.infrastructure.persistence.sqlite.memory_meme_adapters import (
    SQLiteRoomMemoryServiceRepository,
)
from advx_backend.infrastructure.persistence.sqlite.models import (
    ModeMemeEventRow,
    RoomLongTermMemoryRow,
    RoomMemoryEvidenceRow,
    SessionRecordRow,
    SessionViewerInstanceRow,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    PersistedRoomEvent,
    RoomMemoryEvidence,
    RuntimePersistenceConflictError,
    RuntimePersistenceInvariantError,
    SQLiteModeMemeRepository,
    SQLiteRoomEventRepository,
    SQLiteRoomMemoryRepository,
    SQLiteRoomRepository,
    SQLiteSessionRuntimeRepository,
    SQLiteViewerInstanceRepository,
    ViewerInstance,
)


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active_database = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active_database.start()
    try:
        yield active_database
    finally:
        await active_database.close()


@pytest.mark.asyncio
async def test_runtime_start_is_idempotent_and_commit_advances_epoch(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        rooms = SQLiteRoomRepository(session)
        runtimes = SQLiteSessionRuntimeRepository(session)
        await rooms.get_or_create("room-1", display_name="Test room", now_ms=100)
        created, is_new = await runtimes.start(
            session_id="session-1",
            room_id="room-1",
            client_request_id="request-1",
            request_hash="hash-1",
            apply_id="apply-1",
            canonical_spec_json='{"schema_version":1}',
            diff_summary_json="{}",
            app_version="test",
            now_ms=110,
        )
        await SQLiteViewerInstanceRepository(session).add_all(
            [
                ViewerInstance(
                    session_id="session-1",
                    viewer_instance_id="viewer-1",
                    persona_id="persona-1",
                    persona_revision=1,
                    ordinal=0,
                    display_name="Viewer",
                    micro_variant_json="{}",
                    created_epoch=1,
                )
            ]
        )
        committed = await runtimes.commit_revision(
            "session-1",
            1,
            expected_base_revision=0,
            next_epoch=1,
            now_ms=120,
        )
        await session.commit()

    assert is_new is True
    assert created.state == "running"
    assert committed.status == "committed"

    async with database.session_factory() as session:
        runtimes = SQLiteSessionRuntimeRepository(session)
        duplicate, is_new = await runtimes.start(
            session_id="ignored",
            room_id="room-1",
            client_request_id="request-1",
            request_hash="hash-1",
            apply_id="ignored",
            canonical_spec_json="{}",
            diff_summary_json="{}",
            app_version="test",
            now_ms=130,
        )
        assert duplicate.session_id == "session-1"
        assert is_new is False
        with pytest.raises(RuntimePersistenceConflictError):
            await runtimes.get_idempotent_start("request-1", request_hash="other-hash")


@pytest.mark.asyncio
async def test_viewer_ids_cannot_be_reused_within_a_session(
    database: SQLiteDatabase,
) -> None:
    viewer = ViewerInstance(
        session_id="session-1",
        viewer_instance_id="viewer-1",
        persona_id="persona-1",
        persona_revision=1,
        ordinal=0,
        display_name="Viewer",
        micro_variant_json="{}",
        created_epoch=1,
    )
    async with database.session_factory() as session:
        await SQLiteRoomRepository(session).get_or_create(
            "room-1", display_name="Test room", now_ms=100
        )
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
        viewers = SQLiteViewerInstanceRepository(session)
        await viewers.add_all([viewer])
        await viewers.remove("session-1", "viewer-1", removed_epoch=2)
        with pytest.raises(IntegrityError):
            await viewers.add_all([viewer])


@pytest.mark.asyncio
async def test_start_crash_windows_keep_viewer_pool_and_running_state_atomic(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        await SQLiteRoomRepository(session).get_or_create(
            "room-1", display_name="Test room", now_ms=100
        )
        await SQLiteSessionRuntimeRepository(session).start(
            session_id="session-crash",
            room_id="room-1",
            client_request_id="request-crash",
            request_hash="hash-crash",
            apply_id="apply-crash",
            canonical_spec_json="{}",
            diff_summary_json="{}",
            app_version="test",
            now_ms=110,
        )
        await session.commit()

    async with database.session_factory() as session:
        with pytest.raises(
            RuntimePersistenceInvariantError,
            match="persisted active Viewer pool",
        ):
            await SQLiteSessionRuntimeRepository(session).commit_revision(
                "session-crash",
                1,
                expected_base_revision=0,
                next_epoch=1,
                now_ms=120,
            )

    viewer = ViewerInstance(
        session_id="session-crash",
        viewer_instance_id="viewer-crash",
        persona_id="persona-1",
        persona_revision=1,
        ordinal=0,
        display_name="Viewer",
        micro_variant_json="{}",
        created_epoch=1,
    )
    async with database.session_factory() as session:
        await SQLiteViewerInstanceRepository(session).add_all([viewer])
        await SQLiteSessionRuntimeRepository(session).commit_revision(
            "session-crash",
            1,
            expected_base_revision=0,
            next_epoch=1,
            now_ms=120,
        )
        await session.rollback()

    async with database.session_factory() as session:
        state = await session.scalar(
            select(SessionRecordRow.state).where(
                SessionRecordRow.session_id == "session-crash"
            )
        )
        viewer_count = await session.scalar(
            select(func.count())
            .select_from(SessionViewerInstanceRow)
            .where(SessionViewerInstanceRow.session_id == "session-crash")
        )
    assert state == "starting"
    assert viewer_count == 0


@pytest.mark.asyncio
async def test_room_memory_candidate_is_atomic_idempotent_and_evidence_bound(
    database: SQLiteDatabase,
) -> None:
    event = PersistedRoomEvent(
        event_id="event-1",
        room_id="room-1",
        session_id="session-1",
        sequence=1,
        source_type="user_text",
        source_id="message-1",
        audience_epoch=1,
        content_json='{"text":"likes strategy games"}',
        content_hash="event-hash",
        occurred_at_ms=200,
    )
    evidence = [
        RoomMemoryEvidence(
            event_id="event-1",
            source_type="user_text",
            occurred_at_ms=200,
            evidence_summary="User stated a preference.",
        )
    ]
    async with database.session_factory() as session:
        await SQLiteRoomRepository(session).get_or_create(
            "room-1", display_name="Test room", now_ms=100
        )
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
        await SQLiteRoomEventRepository(session).append(event)
        memories = SQLiteRoomMemoryRepository(session)
        result = await memories.commit_candidate(
            candidate_id="candidate-1",
            room_id="room-1",
            idempotency_key="memory-request-1",
            base_revision=0,
            candidate_type="user_preference",
            content="The user likes strategy games.",
            tags=["games"],
            memory_id="memory-1",
            memory_origin="extracted",
            importance=0.8,
            confidence=0.9,
            evidence=evidence,
            now_ms=210,
        )
        duplicate = await memories.commit_candidate(
            candidate_id="candidate-1",
            room_id="room-1",
            idempotency_key="memory-request-1",
            base_revision=0,
            candidate_type="user_preference",
            content="The user likes strategy games.",
            tags=["games"],
            memory_id="memory-1",
            memory_origin="extracted",
            importance=0.8,
            confidence=0.9,
            evidence=evidence,
            now_ms=220,
        )
        with pytest.raises(
            RuntimePersistenceConflictError,
            match="different candidate",
        ):
            await memories.commit_candidate(
                candidate_id="candidate-1",
                room_id="room-1",
                idempotency_key="memory-request-1",
                base_revision=0,
                candidate_type="user_preference",
                content="The user likes strategy games.",
                tags=["games"],
                memory_id="memory-1",
                memory_origin="extracted",
                importance=0.8,
                confidence=0.1,
                evidence=evidence,
                now_ms=230,
            )
        await session.commit()

    assert result == ("memory-1", 1, 1, True)
    assert duplicate == ("memory-1", 1, 1, False)
    async with database.session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(RoomLongTermMemoryRow)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(RoomMemoryEvidenceRow)
        ) == 1
        pruned = await SQLiteRoomEventRepository(session).prune(
            "room-1",
            keep_after_ms=1_000,
            max_events=1,
        )
        await session.commit()

    assert pruned == 1
    async with database.session_factory() as session:
        active = await SQLiteRoomMemoryServiceRepository(session).list_active("room-1")
        assert len(active) == 1
        assert active[0].evidence_event_ids == ["event-1"]
        assert active[0].evidence_event_ids == ["event-1"]

    async with database.session_factory() as session:
        memories = SQLiteRoomMemoryRepository(session)
        with pytest.raises(RuntimePersistenceConflictError):
            await memories.commit_candidate(
                candidate_id="candidate-2",
                room_id="room-1",
                idempotency_key="memory-request-2",
                base_revision=0,
                candidate_type="user_preference",
                content="stale",
                tags=[],
                memory_id="memory-2",
                memory_origin="extracted",
                importance=0.5,
                confidence=0.5,
                evidence=evidence,
                now_ms=230,
            )
        with pytest.raises(RuntimePersistenceInvariantError):
            await memories.commit_candidate(
                candidate_id="candidate-3",
                room_id="room-1",
                idempotency_key="memory-request-3",
                base_revision=1,
                candidate_type="user_preference",
                content="unsupported",
                tags=[],
                memory_id="memory-3",
                memory_origin="extracted",
                importance=0.5,
                confidence=0.5,
                evidence=[],
                now_ms=230,
            )


@pytest.mark.asyncio
async def test_mode_meme_state_and_event_are_committed_together(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        memes = SQLiteModeMemeRepository(session)
        await memes.create(
            meme_id="meme-1",
            event_id="meme-event-1",
            mode_namespace="cs2",
            content="6657",
            intensity=0.6,
            source={"event_ids": ["event-1"]},
            now_ms=100,
        )
        revision = await memes.change_state(
            "meme-1",
            event_id="meme-event-2",
            expected_revision=1,
            state="revoked",
            action="revoked",
            now_ms=110,
        )
        await session.commit()
    assert revision == 2

    async with database.session_factory() as session:
        events = list(
            await session.scalars(
                select(ModeMemeEventRow).order_by(ModeMemeEventRow.created_at_ms)
            )
        )
        assert [(item.action, item.new_revision) for item in events] == [
            ("created", 1),
            ("revoked", 2),
        ]
