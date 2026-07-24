import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore


def runtime_state(
    *,
    epoch: int,
    viewer_sequence: int = 3,
) -> CommittedRuntime:
    viewer = SimpleNamespace(
        viewer_instance_id="viewer-1",
        viewer_sequence=viewer_sequence,
    )
    pool = SimpleNamespace(viewers=[viewer])
    spec = SimpleNamespace(
        room=SimpleNamespace(room_id="room-1"),
        active_mode_id="mode-1",
        modes=[SimpleNamespace(mode_id="mode-1", namespace_id="mode-a")],
    )
    return CommittedRuntime(
        session_id="session-1",
        spec=cast(Any, spec),
        audience_epoch=epoch,
        pool=cast(Any, pool),
    )


@pytest.mark.asyncio
async def test_failed_durable_commit_keeps_previous_runtime_state() -> None:
    store = RuntimeStateStore()
    previous = runtime_state(epoch=1)
    await store.activate(previous)

    async def fail_commit() -> None:
        raise RuntimeError("persistence unavailable")

    with pytest.raises(RuntimeError, match="persistence unavailable"):
        await store.replace_after(runtime_state(epoch=2), fail_commit)

    assert await store.snapshot("session-1") is previous


@pytest.mark.asyncio
async def test_runtime_fence_checks_epoch_viewer_sequence_and_stop() -> None:
    store = RuntimeStateStore()
    await store.activate(runtime_state(epoch=4, viewer_sequence=7))

    assert await store.accepts(
        session_id="session-1",
        audience_epoch=4,
        viewer_instance_id="viewer-1",
        viewer_sequence=7,
    )
    assert not await store.accepts(
        session_id="session-1",
        audience_epoch=3,
        viewer_instance_id="viewer-1",
        viewer_sequence=7,
    )
    assert not await store.accepts(
        session_id="session-1",
        audience_epoch=4,
        viewer_instance_id="viewer-1",
        viewer_sequence=8,
    )
    assert await store.accepts(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=4,
        namespace_id="mode-a",
    )
    assert not await store.accepts(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=4,
        namespace_id="mode-b",
    )

    await store.stop_session("session-1")

    assert not await store.accepts(
        session_id="session-1",
        audience_epoch=4,
    )


@pytest.mark.asyncio
async def test_effect_operation_can_reenter_runtime_state_without_lock_inversion() -> None:
    store = RuntimeStateStore()
    await store.activate(runtime_state(epoch=1))

    async def room_side_effect() -> bool:
        return await store.accepts(
            session_id="session-1",
            audience_epoch=1,
        )

    accepted, result = await asyncio.wait_for(
        store.execute_if_accepting(
            session_id="session-1",
            audience_epoch=1,
            operation=room_side_effect,
        ),
        timeout=1,
    )

    assert accepted is True
    assert result is True


@pytest.mark.asyncio
async def test_debug_snapshot_exposes_a_detached_redacted_runtime_view() -> None:
    store = RuntimeStateStore()
    state = runtime_state(epoch=2, viewer_sequence=4)
    await store.activate(state)

    snapshot = await store.debug_snapshot("session-1")

    assert snapshot.session_id == "session-1"
    assert snapshot.audience_epoch == 2
    assert snapshot.accepting_results is True
    assert snapshot.pool is state.pool
