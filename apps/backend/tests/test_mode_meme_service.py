from types import SimpleNamespace

import pytest

from advx_backend.application.meme_service import ModeMemeService
from advx_backend.domain.meme import (
    MemeCandidate,
    MemeCandidateOutcome,
    ModeMeme,
    ModeMemeState,
)

DAY_MS = 24 * 60 * 60 * 1_000


class Clock:
    def __init__(self, value: int = 40 * DAY_MS) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value


class Fence:
    def __init__(self, epoch: int = 3) -> None:
        self.epoch = epoch

    async def accepts(self, **scope: object) -> bool:
        return scope["audience_epoch"] == self.epoch


def meme(
    meme_id: str,
    *,
    namespace: str = "mode-a",
    updated_at_ms: int = 1,
    pinned: bool = False,
    use_count: int = 0,
) -> ModeMeme:
    return ModeMeme(
        meme_id=meme_id,
        room_id="room-1",
        namespace_id=namespace,
        text=meme_id,
        source_candidate_id=f"candidate-{meme_id}",
        pinned=pinned,
        use_count=use_count,
        revision=1,
        created_at_ms=1,
        updated_at_ms=updated_at_ms,
    )


def candidate(*, epoch: int = 3) -> MemeCandidate:
    return MemeCandidate(
        candidate_id="candidate-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        observation_id="wave-1",
        namespace_id="mode-a",
        text="new meme",
        evidence_event_ids=["event-1"],
        created_at_ms=1,
    )


class Repository:
    def __init__(self) -> None:
        self.items: dict[str, ModeMeme] = {}
        self.pending: list[MemeCandidate] = []
        self.committed: list[MemeCandidate] = []
        self.state_changes: list[tuple[str, ModeMemeState]] = []

    async def list_active(self, namespace_id: str) -> tuple[ModeMeme, ...]:
        del namespace_id
        return tuple(self.items.values())

    async def save_candidate(self, item: MemeCandidate) -> None:
        self.pending.append(item)

    async def commit_candidate(self, item: MemeCandidate) -> object:
        self.committed.append(item)
        return SimpleNamespace(accepted=True, meme_id="meme-1")

    async def change_state(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        state: ModeMemeState,
        action: str,
        now_ms: int,
    ) -> ModeMeme:
        del action
        current = self.items[meme_id]
        assert current.revision == expected_revision
        changed = current.model_copy(
            update={
                "state": state,
                "revision": current.revision + 1,
                "updated_at_ms": now_ms,
            }
        )
        self.items[meme_id] = changed
        self.state_changes.append((meme_id, state))
        return changed

    async def set_pinned(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        pinned: bool,
        now_ms: int,
    ) -> ModeMeme:
        current = self.items[meme_id]
        assert current.revision == expected_revision
        changed = current.model_copy(
            update={
                "pinned": pinned,
                "revision": current.revision + 1,
                "updated_at_ms": now_ms,
            }
        )
        self.items[meme_id] = changed
        return changed

    async def list_archive_candidates(
        self,
        namespace_id: str,
        *,
        inactive_before_ms: int,
    ) -> tuple[ModeMeme, ...]:
        return tuple(
            item
            for item in self.items.values()
            if item.namespace_id == namespace_id and item.updated_at_ms <= inactive_before_ms
        )


def service(repository: Repository, *, epoch: int = 3) -> ModeMemeService:
    return ModeMemeService(
        repository=repository,
        session_fence=Fence(epoch),
        clock=Clock(),
    )


@pytest.mark.asyncio
async def test_namespace_filter_and_candidate_never_emit_barrage() -> None:
    repository = Repository()
    repository.items = {
        "meme-a": meme("meme-a"),
        "meme-b": meme("meme-b", namespace="mode-b"),
    }
    instance = service(repository)

    active = await instance.list_active("mode-a")
    result = await instance.commit_candidate(candidate())

    assert [item.meme_id for item in active] == ["meme-a"]
    assert result.accepted is True
    assert repository.committed == [candidate()]


@pytest.mark.asyncio
async def test_auto_ingest_off_queues_candidate_without_creating_meme() -> None:
    repository = Repository()
    instance = service(repository)
    instance.set_auto_ingest("mode-a", enabled=False)

    result = await instance.commit_candidate(candidate())

    assert result.pending is True
    assert repository.pending == [candidate()]
    assert repository.committed == []


@pytest.mark.asyncio
async def test_old_epoch_candidate_has_zero_side_effects() -> None:
    repository = Repository()

    result = await service(repository).commit_candidate(candidate(epoch=2))

    assert result.reason == "stale_epoch"
    assert repository.pending == []
    assert repository.committed == []


@pytest.mark.asyncio
async def test_already_decided_candidate_has_zero_side_effects() -> None:
    repository = Repository()
    decided = candidate().model_copy(update={"outcome": MemeCandidateOutcome.REJECTED})

    result = await service(repository).commit_candidate(decided)

    assert result.reason == "candidate_not_pending"
    assert repository.pending == []
    assert repository.committed == []


@pytest.mark.asyncio
async def test_lifecycle_and_archive_policy() -> None:
    repository = Repository()
    repository.items = {
        "archive": meme("archive", use_count=2),
        "used": meme("used", use_count=3),
        "pinned": meme("pinned", pinned=True),
        "recent": meme("recent", updated_at_ms=20 * DAY_MS),
    }
    instance = service(repository)

    disabled = await instance.disable("recent", expected_revision=1)
    restored = await instance.restore("recent", expected_revision=2)
    pinned = await instance.set_pinned("recent", expected_revision=3, pinned=True)
    undone = await instance.undo("recent", expected_revision=4)
    archived = await instance.auto_archive("mode-a")

    assert disabled.state is ModeMemeState.DISABLED
    assert restored.state is ModeMemeState.ACTIVE
    assert pinned.pinned is True
    assert undone.state is ModeMemeState.REVOKED
    assert archived == ("archive",)
