from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from advx_backend.application.shared_brain_service import SharedBrainService
from advx_backend.domain.meme import MemeCandidate, ModeMemeState
from advx_backend.domain.room import RoomEvent, RoomEventSource
from advx_backend.infrastructure.persistence.sqlite import DatabaseConfig, SQLiteDatabase
from advx_backend.infrastructure.persistence.sqlite.memory_meme_adapters import (
    SQLiteModeMemeServiceRepository,
)
from advx_backend.infrastructure.persistence.sqlite.models import ModeMemeCandidateRow
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    RuntimePersistenceConflictError,
    RuntimePersistenceInvariantError,
    SQLiteRoomRepository,
    SQLiteSessionRuntimeRepository,
)


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active.start()
    async with active.session_factory() as session:
        await SQLiteRoomRepository(session).get_or_create(
            "room-1",
            display_name="Room",
            now_ms=100,
        )
        await SQLiteSessionRuntimeRepository(session).start(
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
        await session.commit()
    try:
        yield active
    finally:
        await active.close()


class Clock:
    def now_ms(self) -> int:
        return 1_000


class Fence:
    def __init__(self, epoch: int = 1, namespace_id: str = "mode-a") -> None:
        self.epoch = epoch
        self.namespace_id = namespace_id
        self.calls = 0

    async def accepts(self, **scope: object) -> bool:
        self.calls += 1
        return (
            scope["audience_epoch"] == self.epoch
            and scope.get("namespace_id") in {None, self.namespace_id}
        )

    async def execute_if_accepting(
        self,
        *,
        operation: object,
        **scope: object,
    ) -> tuple[bool, object | None]:
        accepted = await self.accepts(**scope)
        if not accepted:
            return False, None
        return True, await operation()  # type: ignore[operator]


class Provenance:
    def observation_wave(self, observation_id: str) -> object | None:
        if observation_id != "wave-1":
            return None
        return SimpleNamespace(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            observation_id=observation_id,
            event_ids=["event-1"],
            trigger_event_ids=["event-1"],
            frame_hashes=[],
        )


class RoomReader:
    async def read_events(self, session_id: str) -> tuple[RoomEvent, ...]:
        assert session_id == "session-1"
        return (
            RoomEvent(
                event_id="event-1",
                session_id=session_id,
                sequence=1,
                source_type=RoomEventSource.USER_TEXT,
                created_at_ms=100,
                text="event",
            ),
        )


def shared_brain(
    database: SQLiteDatabase,
    fence: Fence,
) -> SharedBrainService:
    return SharedBrainService(
        session_factory=database.session_factory,
        runtime_state=fence,
        clock=Clock(),
        room_service=RoomReader(),  # type: ignore[arg-type]
        observation_provenance=Provenance(),
    )


def candidate(
    candidate_id: str,
    *,
    epoch: int = 1,
    namespace_id: str = "mode-a",
) -> MemeCandidate:
    return MemeCandidate(
        candidate_id=candidate_id,
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        observation_id="wave-1",
        namespace_id=namespace_id,
        text=candidate_id,
        evidence_event_ids=["event-1"],
        created_at_ms=200,
    )


@pytest.mark.asyncio
async def test_database_migrates_to_persistent_meme_candidate_head(
    database: SQLiteDatabase,
) -> None:
    async with database.session_factory() as session:
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        tables = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name LIKE 'mode_meme_%'"
                    )
                )
            )
        }

    assert revision == "0005_detach_memory_evidence_events"
    assert {"mode_meme_candidates", "mode_meme_settings"} <= tables


@pytest.mark.asyncio
async def test_pending_candidate_and_toggle_survive_service_restart(
    database: SQLiteDatabase,
) -> None:
    service = shared_brain(database, Fence())
    setting = await service.set_auto_ingest(
        "mode-a",
        enabled=False,
        expected_revision=0,
    )
    result = await service.commit_meme_candidate(candidate("pending"))

    restarted = shared_brain(database, Fence())

    assert setting.revision == 1
    assert result.pending is True
    assert await restarted.get_auto_ingest("mode-a") == setting
    assert await restarted.list_pending_candidates("mode-a") == (
        candidate("pending"),
    )
    assert await restarted.list_active_memes("mode-a") == ()

    with pytest.raises(RuntimePersistenceConflictError):
        await restarted.set_auto_ingest(
            "mode-a",
            enabled=True,
            expected_revision=0,
        )


@pytest.mark.asyncio
async def test_stale_candidate_has_no_persistent_side_effects(
    database: SQLiteDatabase,
) -> None:
    fence = Fence(epoch=2)
    service = shared_brain(database, fence)

    result = await service.commit_meme_candidate(candidate("stale", epoch=1))

    assert result.reason == "stale_epoch"
    assert await service.list_pending_candidates("mode-a") == ()
    assert await service.list_memes("mode-a") == ()


@pytest.mark.asyncio
async def test_pending_candidate_approval_rechecks_runtime_fence(
    database: SQLiteDatabase,
) -> None:
    accepting = shared_brain(database, Fence(epoch=1))
    await accepting.set_auto_ingest("mode-a", enabled=False, expected_revision=0)
    pending = await accepting.commit_meme_candidate(candidate("pending-stale", epoch=1))
    assert pending.pending is True
    async with database.session_factory() as session:
        before = await session.get(ModeMemeCandidateRow, "pending-stale")
        assert before is not None
        before_updated_at_ms = before.updated_at_ms

    restarted = shared_brain(database, Fence(epoch=2))
    result = await restarted.approve_meme_candidate("mode-a", "pending-stale")

    assert result.accepted is False
    assert result.reason == "stale_epoch"
    assert await restarted.list_memes("mode-a") == ()
    assert await restarted.list_pending_candidates("mode-a") == (
        candidate("pending-stale"),
    )
    async with database.session_factory() as session:
        stored = await SQLiteModeMemeServiceRepository(session).get_candidate(
            "mode-a",
            "pending-stale",
        )
    assert stored.outcome.value == "pending"
    async with database.session_factory() as session:
        after = await session.get(ModeMemeCandidateRow, "pending-stale")
        assert after is not None
        assert after.updated_at_ms == before_updated_at_ms


@pytest.mark.asyncio
async def test_candidate_requested_namespace_mismatch_has_no_side_effects(
    database: SQLiteDatabase,
) -> None:
    service = shared_brain(database, Fence())
    await service.set_auto_ingest("mode-a", enabled=False, expected_revision=0)
    await service.commit_meme_candidate(candidate("namespace-bound"))

    with pytest.raises(
        RuntimePersistenceInvariantError,
        match="meme candidate is missing",
    ):
        await service.approve_meme_candidate("mode-b", "namespace-bound")
    with pytest.raises(
        RuntimePersistenceInvariantError,
        match="meme candidate is missing",
    ):
        await service.reject_meme_candidate("mode-b", "namespace-bound")

    assert await service.list_pending_candidates("mode-a") == (
        candidate("namespace-bound"),
    )
    assert await service.list_pending_candidates("mode-b") == ()
    assert await service.list_memes("mode-a") == ()
    assert await service.list_memes("mode-b") == ()


@pytest.mark.asyncio
async def test_active_namespace_fence_blocks_auto_ingest_approval_and_rejection(
    database: SQLiteDatabase,
) -> None:
    wrong_mode = shared_brain(database, Fence(namespace_id="mode-b"))
    auto_result = await wrong_mode.commit_meme_candidate(
        candidate("auto-wrong-mode")
    )
    assert auto_result.accepted is False
    assert auto_result.reason == "stale_epoch"
    assert await wrong_mode.list_pending_candidates("mode-a") == ()

    accepting = shared_brain(database, Fence())
    await accepting.set_auto_ingest("mode-a", enabled=False, expected_revision=0)
    await accepting.commit_meme_candidate(candidate("pending-wrong-mode"))

    approval = await wrong_mode.approve_meme_candidate(
        "mode-a",
        "pending-wrong-mode",
    )
    assert approval.accepted is False
    assert approval.reason == "stale_epoch"
    with pytest.raises(
        RuntimePersistenceInvariantError,
        match="runtime scope is stale",
    ):
        await wrong_mode.reject_meme_candidate("mode-a", "pending-wrong-mode")
    assert await wrong_mode.list_pending_candidates("mode-a") == (
        candidate("pending-wrong-mode"),
    )
    assert await wrong_mode.list_memes("mode-a") == ()


@pytest.mark.asyncio
async def test_undo_rechecks_source_candidate_namespace_and_runtime_fence(
    database: SQLiteDatabase,
) -> None:
    accepting = shared_brain(database, Fence(namespace_id="mode-c"))
    created = await accepting.commit_meme_candidate(
        candidate("undo-bound", namespace_id="mode-c")
    )
    assert created.meme_id == "meme:undo-bound"

    wrong_mode = shared_brain(database, Fence(namespace_id="mode-b"))
    with pytest.raises(
        RuntimePersistenceInvariantError,
        match="runtime scope is stale",
    ):
        await wrong_mode.undo_meme(
            "mode-c",
            "meme:undo-bound",
            expected_revision=1,
        )

    stored = await wrong_mode.list_memes("mode-c")
    assert len(stored) == 1
    assert stored[0].state is ModeMemeState.ACTIVE
    assert stored[0].revision == 1


@pytest.mark.asyncio
async def test_meme_management_is_cas_and_namespace_scoped(
    database: SQLiteDatabase,
) -> None:
    service = shared_brain(database, Fence())
    created = await service.commit_meme_candidate(candidate("active"))
    assert created.meme_id == "meme:active"

    pinned = await service.pin_meme("mode-a", "meme:active", expected_revision=1)
    disabled = await service.disable_meme(
        "mode-a",
        "meme:active",
        expected_revision=pinned.revision,
    )
    restored = await service.restore_meme(
        "mode-a",
        "meme:active",
        expected_revision=disabled.revision,
    )
    revoked = await service.undo_meme(
        "mode-a",
        "meme:active",
        expected_revision=restored.revision,
    )

    assert revoked.state is ModeMemeState.REVOKED
    assert await service.list_active_memes("mode-a") == ()
    with pytest.raises(RuntimePersistenceConflictError):
        await service.archive_meme("mode-a", "meme:active", expected_revision=1)
