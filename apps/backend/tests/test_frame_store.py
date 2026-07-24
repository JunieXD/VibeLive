import pytest

from advx_backend.application.frame_store import InMemoryFrameStore
from advx_backend.application.ports.ingest import FrameInput, FrameStoreLimits
from advx_backend.domain.observation import FrameRef


class SequentialIdGenerator:
    def __init__(self) -> None:
        self._next = 0

    def new_id(self) -> str:
        self._next += 1
        return f"frame-{self._next}"


async def _store(
    store: InMemoryFrameStore,
    *,
    input_id: str,
    captured_at_ms: int,
) -> FrameRef:
    return await store.store(
        FrameInput(
            session_id="session-1",
            input_id=input_id,
            captured_at_ms=captured_at_ms,
            mime_type="image/png",
            body=input_id.encode(),
        )
    )


@pytest.mark.asyncio
async def test_retained_frames_survive_lru_eviction_until_released() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(max_frames=2, max_frame_bytes=100, max_total_bytes=100),
        id_generator=SequentialIdGenerator(),
    )
    await store.start_session("session-1")
    first = await _store(store, input_id="first", captured_at_ms=1)
    second = await _store(store, input_id="second", captured_at_ms=2)

    assert await store.retain(session_id="session-1", frames=(first,))
    await _store(store, input_id="third", captured_at_ms=3)

    assert await store.resolve(session_id="session-1", frame=first) is not None
    assert await store.resolve(session_id="session-1", frame=second) is None

    await store.release(session_id="session-1", frames=(first,))
    await _store(store, input_id="fourth", captured_at_ms=4)
    await _store(store, input_id="fifth", captured_at_ms=5)

    assert await store.resolve(session_id="session-1", frame=first) is None
