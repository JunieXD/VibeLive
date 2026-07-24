import pytest

from advx_backend.application.memory_service import RoomMemoryService
from advx_backend.application.ports.memory import (
    MemoryCommitResult,
    MemoryEvidence,
    RoomMemoryCandidate,
)
from advx_backend.domain.memory import (
    RoomLongTermMemory,
    RoomMemorySlice,
    RoomMemoryType,
)


class FixedClock:
    def now_ms(self) -> int:
        return 10_000


class Fence:
    def __init__(self, epoch: int = 2) -> None:
        self.epoch = epoch

    async def accepts(self, **scope: object) -> bool:
        return scope["audience_epoch"] == self.epoch


class Events:
    def __init__(self, items: tuple[MemoryEvidence, ...]) -> None:
        self.items = items
        self.reads = 0

    async def read_events(
        self,
        event_ids: tuple[str, ...],
    ) -> tuple[MemoryEvidence, ...]:
        self.reads += 1
        requested = set(event_ids)
        return tuple(item for item in self.items if item.event_id in requested)


class Repository:
    def __init__(self) -> None:
        self.commits: list[RoomMemoryCandidate] = []
        self.active: dict[str, RoomLongTermMemory] = {}
        self.revision = 0
        self.deleted: list[str] = []

    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice:
        del event_ids
        return RoomMemorySlice(
            room_id=room_id,
            memory_revision=self.revision,
            memory_ids=list(self.active)[:limit],
        )

    async def commit_candidate(
        self,
        candidate: RoomMemoryCandidate,
        *,
        evidence: tuple[MemoryEvidence, ...],
        now_ms: int,
    ) -> MemoryCommitResult:
        del evidence, now_ms
        self.commits.append(candidate)
        self.revision += 1
        return MemoryCommitResult(
            accepted=True,
            memory_id=candidate.memory_id,
            memory_revision=self.revision,
            created=True,
        )

    async def list_active(self, room_id: str) -> tuple[RoomLongTermMemory, ...]:
        return tuple(item for item in self.active.values() if item.room_id == room_id)

    async def revoke(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> RoomLongTermMemory:
        current = self.active[memory_id]
        assert current.room_id == room_id
        assert current.revision == expected_revision
        revoked = current.model_copy(
            update={
                "revision": current.revision + 1,
                "updated_at_ms": now_ms,
                "revoked_at_ms": now_ms,
            }
        )
        self.active[memory_id] = revoked
        return revoked

    async def delete(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> bool:
        del now_ms
        item = self.active.get(memory_id)
        if item is None or item.room_id != room_id:
            return False
        assert item.revision == expected_revision
        del self.active[memory_id]
        self.deleted.append(memory_id)
        return True

    async def reset(
        self,
        room_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> int:
        del expected_revision, now_ms
        removed = [key for key, item in self.active.items() if item.room_id == room_id]
        for key in removed:
            del self.active[key]
        return len(removed)


def candidate(memory_type: RoomMemoryType, *, epoch: int = 2) -> RoomMemoryCandidate:
    return RoomMemoryCandidate(
        candidate_id="candidate-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        idempotency_key="request-1",
        base_revision=0,
        memory_id="memory-1",
        memory_type=memory_type,
        content="The user likes tactical games.",
        evidence_event_ids=("event-1",),
    )


def service(
    repository: Repository,
    events: Events,
    *,
    epoch: int = 2,
) -> RoomMemoryService:
    return RoomMemoryService(
        repository=repository,
        event_reader=events,
        session_fence=Fence(epoch),
        clock=FixedClock(),
    )


@pytest.mark.asyncio
async def test_user_fact_requires_real_non_ai_evidence() -> None:
    repository = Repository()
    events = Events(
        (
            MemoryEvidence(
                event_id="event-1",
                room_id="room-1",
                source_type="viewer_barrage",
                occurred_at_ms=100,
            ),
        )
    )

    result = await service(repository, events).commit_candidate(
        candidate(RoomMemoryType.USER_PREFERENCE)
    )

    assert result == MemoryCommitResult(
        accepted=False,
        reason="non_ai_evidence_required",
    )
    assert repository.commits == []


@pytest.mark.asyncio
async def test_ai_only_evidence_can_commit_room_lore() -> None:
    repository = Repository()
    events = Events(
        (
            MemoryEvidence(
                event_id="event-1",
                room_id="room-1",
                source_type="viewer_barrage",
                occurred_at_ms=100,
            ),
        )
    )

    result = await service(repository, events).commit_candidate(
        candidate(RoomMemoryType.ROOM_LORE)
    )

    assert result.accepted is True
    assert [item.memory_id for item in repository.commits] == ["memory-1"]


@pytest.mark.asyncio
async def test_old_epoch_candidate_has_zero_side_effects() -> None:
    repository = Repository()
    events = Events(())

    result = await service(repository, events).commit_candidate(
        candidate(RoomMemoryType.ROOM_LORE, epoch=1)
    )

    assert result.reason == "stale_epoch"
    assert events.reads == 0
    assert repository.commits == []


@pytest.mark.asyncio
async def test_room_slice_and_management_never_take_viewer_or_mode_scope() -> None:
    repository = Repository()
    repository.active["memory-1"] = RoomLongTermMemory(
        memory_id="memory-1",
        room_id="room-1",
        memory_type=RoomMemoryType.SHARED_EXPERIENCE,
        content="Shared win.",
        evidence_event_ids=["event-1"],
        confidence=0.8,
        revision=1,
        created_at_ms=1,
        updated_at_ms=1,
    )
    instance = service(repository, Events(()))

    first = await instance.read_slice(room_id="room-1", event_ids=("a",), limit=8)
    second = await instance.read_slice(room_id="room-1", event_ids=("b",), limit=8)
    revoked = await instance.revoke("room-1", "memory-1", expected_revision=1)
    deleted = await instance.delete("room-1", "memory-1", expected_revision=2)

    assert first == second
    assert revoked.revoked_at_ms == 10_000
    assert deleted is True
