import asyncio

import pytest

from advx_backend.application.session_service import (
    InvalidSessionStateError,
    SessionAlreadyActiveError,
    SessionNotFoundError,
    SessionService,
)
from advx_backend.domain.session import SessionState, SessionStatus


class IncrementingClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now_ms(self) -> int:
        self.value += 1
        return self.value


class SequenceIdGenerator:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"session-{self.value}"


class RecordingPublisher:
    def __init__(self) -> None:
        self.statuses: list[SessionStatus] = []

    async def publish_session_status(self, status: SessionStatus) -> None:
        self.statuses.append(status)


class BlockingStoppingPublisher(RecordingPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.stopping_started = asyncio.Event()

    async def publish_session_status(self, status: SessionStatus) -> None:
        await super().publish_session_status(status)
        if status.state is SessionState.STOPPING:
            self.stopping_started.set()
            await asyncio.Event().wait()


def create_service() -> tuple[SessionService, RecordingPublisher]:
    publisher = RecordingPublisher()
    service = SessionService(
        clock=IncrementingClock(),
        id_generator=SequenceIdGenerator(),
        publisher=publisher,
    )
    return service, publisher


@pytest.mark.asyncio
async def test_session_lifecycle_is_ordered_and_single_active() -> None:
    service, publisher = create_service()

    initial = await service.status()
    assert initial.state is SessionState.IDLE
    assert initial.session_id is None
    assert initial.revision == 0

    running = await service.start()
    assert running.session_id == "session-1"
    assert running.state is SessionState.RUNNING
    assert running.revision == 2

    with pytest.raises(SessionAlreadyActiveError):
        await service.start()

    paused = await service.pause("session-1")
    resumed = await service.resume("session-1")
    idle = await service.stop("session-1")

    assert paused.state is SessionState.PAUSED
    assert resumed.state is SessionState.RUNNING
    assert idle.state is SessionState.IDLE
    assert idle.session_id is None
    assert idle.started_at_ms is None
    assert [status.state for status in publisher.statuses] == [
        SessionState.STARTING,
        SessionState.RUNNING,
        SessionState.PAUSED,
        SessionState.RUNNING,
        SessionState.STOPPING,
        SessionState.IDLE,
    ]
    assert [status.revision for status in publisher.statuses] == list(range(1, 7))


@pytest.mark.asyncio
async def test_pause_cancels_session_tasks_and_rejects_results() -> None:
    service, _ = create_service()
    running = await service.start()
    assert running.session_id is not None
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = await service.start_task(running.session_id, worker, name="test-worker")
    await started.wait()
    await service.pause(running.session_id)

    assert task.cancelled()
    assert cancelled.is_set()
    assert await service.accepts_results(running.session_id) is False

    await service.resume(running.session_id)
    assert await service.accepts_results(running.session_id) is True


@pytest.mark.asyncio
async def test_old_session_results_are_rejected_after_stop() -> None:
    service, _ = create_service()
    first = await service.start()
    assert first.session_id is not None
    await service.stop(first.session_id)

    second = await service.start()

    assert second.session_id == "session-2"
    assert await service.accepts_results(first.session_id) is False
    assert await service.accepts_results(second.session_id) is True


@pytest.mark.asyncio
async def test_invalid_transition_and_unknown_session_are_distinct() -> None:
    service, _ = create_service()
    running = await service.start()
    assert running.session_id is not None

    with pytest.raises(InvalidSessionStateError):
        await service.resume(running.session_id)

    with pytest.raises(SessionNotFoundError):
        await service.pause("not-current")


@pytest.mark.asyncio
async def test_service_retrieves_errors_from_completed_session_tasks() -> None:
    service, _ = create_service()
    running = await service.start()
    assert running.session_id is not None

    async def failing_worker() -> None:
        raise RuntimeError("worker failed")

    task = await service.start_task(running.session_id, failing_worker)
    await asyncio.sleep(0)
    assert task.done()

    stopped = await service.stop(running.session_id)

    assert stopped.state is SessionState.IDLE


@pytest.mark.asyncio
async def test_cancelled_stop_request_still_finishes_cleanup() -> None:
    publisher = BlockingStoppingPublisher()
    service = SessionService(
        clock=IncrementingClock(),
        id_generator=SequenceIdGenerator(),
        publisher=publisher,
    )
    running = await service.start()
    assert running.session_id is not None

    stop_task = asyncio.create_task(service.stop(running.session_id))
    await publisher.stopping_started.wait()
    stop_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stop_task

    status = await service.status()
    assert status.state is SessionState.IDLE
    assert status.session_id is None
