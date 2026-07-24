from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from advx_backend.domain.meme import MemeCandidate, ModeMeme
from advx_backend.domain.memory import (
    RoomLongTermMemory,
    RoomMemorySlice,
    RoomMemoryType,
    RoomWorkingMemory,
)


class FixedClock:
    def now_ms(self) -> int:
        return 1_000


class Fence:
    def __init__(self, *, epoch: int) -> None:
        self.epoch = epoch

    async def accepts(self, **scope: object) -> bool:
        return scope["audience_epoch"] == self.epoch


def test_room_working_memory_contains_shared_event_ids_without_viewer_ownership() -> None:
    memory = RoomWorkingMemory(
        room_id="room-1",
        session_id="session-1",
        revision=2,
        event_ids=["user-1", "viewer-a-1", "viewer-b-1"],
        updated_at_ms=1_000,
    )

    assert memory.event_ids == ["user-1", "viewer-a-1", "viewer-b-1"]
    with pytest.raises(ValidationError, match="viewer_instance_id"):
        RoomWorkingMemory(
            room_id="room-1",
            session_id="session-1",
            revision=2,
            event_ids=["viewer-a-1"],
            updated_at_ms=1_000,
            viewer_instance_id="viewer-a",
        )


def test_room_long_term_memory_is_room_owned_not_viewer_or_mode_owned() -> None:
    values = {
        "memory_id": "memory-1",
        "room_id": "room-1",
        "memory_type": RoomMemoryType.USER_PREFERENCE,
        "content": "likes tactical shooters",
        "evidence_event_ids": ["user-1"],
        "confidence": 0.9,
        "revision": 1,
        "created_at_ms": 1_000,
        "updated_at_ms": 1_000,
    }

    memory = RoomLongTermMemory(**values)

    assert memory.room_id == "room-1"
    for forbidden_owner in ("viewer_instance_id", "persona_id", "mode_namespace"):
        with pytest.raises(ValidationError, match=forbidden_owner):
            RoomLongTermMemory(**values, **{forbidden_owner: "private-owner"})


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self.by_room = {
            "room-1": RoomMemorySlice(
                room_id="room-1",
                memory_revision=3,
                memory_ids=["memory-1"],
            )
        }

    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice:
        del event_ids, limit
        return self.by_room[room_id]


class EmptyEventReader:
    async def read_events(self, event_ids: tuple[str, ...]) -> tuple[object, ...]:
        del event_ids
        return ()


@pytest.mark.asyncio
async def test_long_term_memory_slice_is_shared_across_sessions_and_modes_in_a_room() -> None:
    from advx_backend.application.memory_service import RoomMemoryService

    service = RoomMemoryService(
        repository=InMemoryMemoryRepository(),
        event_reader=EmptyEventReader(),
        session_fence=Fence(epoch=1),
        clock=FixedClock(),
    )

    session_one = await service.read_slice(
        room_id="room-1",
        event_ids=("session-1-event",),
        limit=8,
    )
    session_two_other_mode = await service.read_slice(
        room_id="room-1",
        event_ids=("session-2-other-mode-event",),
        limit=8,
    )

    assert session_one == session_two_other_mode
    assert session_one.memory_ids == ["memory-1"]


def meme(meme_id: str, namespace: str) -> ModeMeme:
    return ModeMeme(
        meme_id=meme_id,
        room_id="room-1",
        namespace_id=namespace,
        text=meme_id,
        source_candidate_id=f"candidate-{meme_id}",
        revision=1,
        created_at_ms=1_000,
        updated_at_ms=1_000,
    )


class InMemoryMemeRepository:
    def __init__(self) -> None:
        self.items = {
            "mode-a": (meme("meme-a", "mode-a"),),
            "mode-b": (meme("meme-b", "mode-b"),),
        }
        self.committed: list[MemeCandidate] = []

    async def list_active(self, namespace_id: str) -> tuple[ModeMeme, ...]:
        return self.items.get(namespace_id, ())

    async def commit_candidate(self, candidate: MemeCandidate) -> object:
        self.committed.append(candidate)
        return SimpleNamespace(accepted=True, meme_id=f"meme-{candidate.candidate_id}")


@pytest.mark.asyncio
async def test_mode_memes_never_cross_namespace_boundaries() -> None:
    from advx_backend.application.meme_service import ModeMemeService

    service = ModeMemeService(
        repository=InMemoryMemeRepository(),
        session_fence=Fence(epoch=1),
        clock=FixedClock(),
    )

    mode_a = await service.list_active("mode-a")
    mode_b = await service.list_active("mode-b")

    assert [item.meme_id for item in mode_a] == ["meme-a"]
    assert [item.meme_id for item in mode_b] == ["meme-b"]
    assert {item.namespace_id for item in mode_a} == {"mode-a"}
    assert {item.namespace_id for item in mode_b} == {"mode-b"}


@pytest.mark.asyncio
async def test_old_epoch_meme_candidate_has_zero_repository_side_effects() -> None:
    from advx_backend.application.meme_service import ModeMemeService

    repository = InMemoryMemeRepository()
    service = ModeMemeService(
        repository=repository,
        session_fence=Fence(epoch=2),
        clock=FixedClock(),
    )
    candidate = MemeCandidate(
        candidate_id="candidate-old",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="wave-1",
        namespace_id="mode-a",
        text="old result",
        evidence_event_ids=["event-1"],
        created_at_ms=100,
    )

    result = await service.commit_candidate(candidate)

    assert result.accepted is False
    assert repository.committed == []
