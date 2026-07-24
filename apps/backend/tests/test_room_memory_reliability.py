import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from advx_backend.application.memory_extractor import OpenAICompatibleMemoryExtractor
from advx_backend.application.ports.memory import MemoryEvidence, RoomMemoryCandidate
from advx_backend.application.shared_brain_service import (
    SharedBrainService,
    _LoopOwnedRoomLockRegistry,
)
from advx_backend.contracts.viewer_runtime import ProviderRuntimeSpec
from advx_backend.domain.memory import RoomMemoryType
from advx_backend.infrastructure.persistence.sqlite import (
    DatabaseConfig,
    RuntimePersistenceConflictError,
    SQLiteDatabase,
    SQLiteRoomMemoryServiceRepository,
    SQLiteRoomRepository,
)
from advx_backend.infrastructure.persistence.sqlite.models import (
    RoomEventRow,
    SessionRecordRow,
)
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000

    def now_ms(self) -> int:
        self.value += 1
        return self.value


class _AcceptedFence:
    async def accepts(self, **scope: object) -> bool:
        del scope
        return True

    async def execute_if_accepting(
        self,
        *,
        operation: Callable[[], Awaitable[object]],
        **scope: object,
    ) -> tuple[bool, object | None]:
        del scope
        return True, await operation()


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active.start()
    try:
        async with active.session_factory() as session:
            await SQLiteRoomRepository(session).get_or_create(
                "room-1",
                display_name="Room",
                now_ms=1,
            )
            session.add(
                SessionRecordRow(
                    session_id="session-1",
                    room_id="room-1",
                    state="running",
                    audience_epoch=1,
                    active_config_hash="a" * 64,
                    recovery_json=None,
                    session_seed="seed",
                    next_creation_ordinal=1,
                    target_concurrent_viewers=1,
                    population_revision=1,
                    controller_state_json="{}",
                    client_request_id="request-1",
                    client_request_hash="b" * 64,
                    started_at_ms=1,
                    ended_at_ms=None,
                    outcome=None,
                    app_version="test",
                )
            )
            await session.flush()
            content_json = json.dumps({"text": "public evidence"})
            session.add(
                RoomEventRow(
                    event_id="event-1",
                    room_id="room-1",
                    session_id="session-1",
                    sequence=1,
                    source_type="user_text",
                    source_id="host",
                    audience_epoch=1,
                    content_json=content_json,
                    content_hash=hashlib.sha256(content_json.encode()).hexdigest(),
                    occurred_at_ms=2,
                )
            )
            await session.commit()
        yield active
    finally:
        await active.close()


def _candidate(index: int) -> RoomMemoryCandidate:
    return RoomMemoryCandidate(
        candidate_id=f"candidate-{index}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        idempotency_key=f"idempotency-{index}",
        base_revision=0,
        memory_id=f"memory-{index}",
        memory_type=RoomMemoryType.SHARED_EXPERIENCE,
        content=f"memory {index}",
        evidence_event_ids=("event-1",),
    )


def test_memory_commit_locks_rebind_after_event_loop_restart() -> None:
    owner = _LoopOwnedRoomLockRegistry()
    loop_ids: list[int] = []

    async def contend() -> None:
        lock = owner.for_running_loop().lock_for("room-1")
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()

        async def hold() -> None:
            async with lock:
                holder_entered.set()
                await release_holder.wait()

        async def wait() -> None:
            await holder_entered.wait()
            async with lock:
                loop_ids.append(id(asyncio.get_running_loop()))

        holder = asyncio.create_task(hold())
        waiter = asyncio.create_task(wait())
        await holder_entered.wait()
        await asyncio.sleep(0)
        release_holder.set()
        await asyncio.gather(holder, waiter)

    asyncio.run(contend())
    asyncio.run(contend())

    assert len(set(loop_ids)) == 2


@pytest.mark.asyncio
async def test_concurrent_room_memory_commits_are_monotonic_and_idempotent(
    database: SQLiteDatabase,
) -> None:
    services = (
        SharedBrainService(
            session_factory=database.session_factory,
            runtime_state=_AcceptedFence(),
            clock=_Clock(),
        ),
        SharedBrainService(
            session_factory=database.session_factory,
            runtime_state=_AcceptedFence(),
            clock=_Clock(),
        ),
    )
    candidates = [_candidate(index) for index in range(24)]

    results = await asyncio.gather(
        *(
            services[index % len(services)].commit_memory_candidate(candidate)
            for index, candidate in enumerate(candidates)
        )
    )

    assert all(result.accepted for result in results)
    assert sorted(result.head_revision for result in results) == list(range(1, 25))
    assert len({result.memory_id for result in results}) == 24
    assert await services[0].get_memory_head("room-1") == 24
    assert len(await services[1].list_memories("room-1")) == 24

    replayed = await asyncio.gather(
        *(
            services[index % len(services)].commit_memory_candidate(candidate)
            for index, candidate in enumerate(candidates)
        )
    )
    assert all(result.accepted and not result.created for result in replayed)
    assert await services[0].get_memory_head("room-1") == 24
    assert len(await services[1].list_memories("room-1")) == 24


@pytest.mark.asyncio
async def test_second_memory_head_conflict_is_a_normal_rejection(
    database: SQLiteDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def always_stale(
        self: SQLiteRoomMemoryServiceRepository,
        candidate: RoomMemoryCandidate,
        *,
        evidence: object,
        now_ms: int,
    ) -> object:
        del self, candidate, evidence, now_ms
        nonlocal attempts
        attempts += 1
        raise RuntimePersistenceConflictError("room memory head is stale")

    monkeypatch.setattr(
        SQLiteRoomMemoryServiceRepository,
        "commit_candidate",
        always_stale,
    )
    service = SharedBrainService(
        session_factory=database.session_factory,
        runtime_state=_AcceptedFence(),
        clock=_Clock(),
    )

    result = await service.commit_memory_candidate(_candidate(1))

    assert attempts == 2
    assert not result.accepted
    assert result.reason == "stale_head"


@pytest.mark.asyncio
async def test_memory_extraction_rejects_only_invalid_candidates() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "memory_type": "shared_experience",
                                            "content": "valid",
                                            "evidence_event_ids": ["event-1"],
                                            "importance": 0.7,
                                            "confidence": 0.8,
                                        },
                                        {
                                            "memory_type": "shared_experience",
                                            "content": "private",
                                            "evidence_event_ids": ["private-event"],
                                            "importance": 0.7,
                                            "confidence": 0.8,
                                        },
                                        {
                                            "memory_type": "shared_experience",
                                            "content": "",
                                            "evidence_event_ids": ["event-1"],
                                            "importance": 0.7,
                                            "confidence": 0.8,
                                        },
                                        "malformed",
                                    ]
                                }
                            )
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = OpenAICompatibleMemoryExtractor(
            OpenAICompatibleViewerRuntimeConfig(
                base_url="https://example.com/v1",
                provider=ProviderRuntimeSpec(
                    provider_profile_id="profile-1",
                    viewer_model="viewer",
                    memory_model="memory",
                    visual_summary_model="visual",
                ),
                api_key="secret",
            ),
            client=client,
        )
        candidates = await extractor.extract(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            events=[
                MemoryEvidence(
                    event_id="event-1",
                    room_id="room-1",
                    source_type="user_text",
                    occurred_at_ms=1,
                )
            ],
            current_revision=0,
        )
        await extractor.aclose()

    assert len(candidates) == 1
    assert candidates[0].content == "valid"
