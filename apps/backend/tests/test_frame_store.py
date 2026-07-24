import pytest

from advx_backend.application.frame_store import (
    FrameStoreSessionNotActiveError,
    FrameTooLargeError,
    InMemoryFrameStore,
)
from advx_backend.application.ports.ingest import FrameInput, FrameStoreLimits


class SequenceIdGenerator:
    def __init__(self) -> None:
        self._next = 1

    def new_id(self) -> str:
        value = f"frame-{self._next}"
        self._next += 1
        return value


def frame(
    input_id: str,
    body: bytes,
    *,
    captured_at_ms: int = 100,
    change_score: float | None = None,
) -> FrameInput:
    return FrameInput(
        session_id="session-1",
        input_id=input_id,
        captured_at_ms=captured_at_ms,
        mime_type="image/jpeg",
        body=body,
        change_score=change_score,
    )


@pytest.mark.asyncio
async def test_frame_store_evicts_oldest_by_count_and_total_bytes() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(max_frames=2, max_frame_bytes=4, max_total_bytes=6),
        id_generator=SequenceIdGenerator(),
    )
    await store.start_session("session-1")

    first = await store.store(frame("input-1", b"111"))
    second = await store.store(frame("input-2", b"222"))
    third = await store.store(frame("input-3", b"333"))

    assert await store.resolve(session_id="session-1", frame=first) is None
    assert (await store.resolve(session_id="session-1", frame=second)) is not None
    resolved = await store.resolve(session_id="session-1", frame=third)
    assert resolved is not None
    assert resolved.body == b"333"


@pytest.mark.asyncio
async def test_frame_store_rejects_oversized_and_cross_session_access() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(max_frames=2, max_frame_bytes=8, max_total_bytes=4),
        id_generator=SequenceIdGenerator(),
    )
    await store.start_session("session-1")

    with pytest.raises(FrameTooLargeError):
        await store.store(frame("input-1", b"12345"))
    stored = await store.store(frame("input-2", b"1234"))
    with pytest.raises(FrameStoreSessionNotActiveError):
        await store.resolve(session_id="session-2", frame=stored)

    await store.stop_session("session-1")
    with pytest.raises(FrameStoreSessionNotActiveError):
        await store.store(frame("input-3", b"1"))


@pytest.mark.asyncio
async def test_frame_store_prefers_validated_visual_score_and_falls_back_when_missing() -> None:
    store = InMemoryFrameStore(
        limits=FrameStoreLimits(max_frames=4, max_frame_bytes=8, max_total_bytes=32),
        id_generator=SequenceIdGenerator(),
    )
    await store.start_session("session-1")

    first = await store.store(frame("input-1", b"aaaa", change_score=0.0))
    encoded_differently = await store.store(
        frame("input-2", b"zzzz", captured_at_ms=200, change_score=0.02)
    )
    fallback = await store.store(frame("input-3", b"yyyy", captured_at_ms=300))

    first_resolved = await store.resolve(session_id="session-1", frame=first)
    visual_resolved = await store.resolve(
        session_id="session-1",
        frame=encoded_differently,
    )
    fallback_resolved = await store.resolve(session_id="session-1", frame=fallback)
    assert first_resolved is not None and first_resolved.change_score == 0.0
    assert visual_resolved is not None and visual_resolved.change_score == 0.02
    assert fallback_resolved is not None and fallback_resolved.change_score > 0.0
