import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import pytest

from advx_backend.application.reaction_scheduler import (
    LatestWinsReactionScheduler,
    ReactionSchedulerConfig,
)
from advx_backend.application.reaction_service import ReactionResult
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEvent, RoomEventSource

T = TypeVar("T")


class FakeClock:
    def __init__(self, now_ms: int) -> None:
        self.now_ms_value = now_ms

    def now_ms(self) -> int:
        return self.now_ms_value


class ManagedSessionTasks:
    def __init__(self, active_session_ids: set[str]) -> None:
        self.active_session_ids = set(active_session_ids)
        self._tasks: dict[str, set[asyncio.Task[Any]]] = {}

    async def start_task(
        self,
        session_id: str,
        factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        if session_id not in self.active_session_ids:
            raise RuntimeError("session is not accepting work")
        task = asyncio.create_task(factory(), name=name)
        session_tasks = self._tasks.setdefault(session_id, set())
        session_tasks.add(task)
        task.add_done_callback(session_tasks.discard)
        return task

    async def accepts_results(self, session_id: str) -> bool:
        return session_id in self.active_session_ids

    async def pause(self, session_id: str) -> None:
        await self._cancel_session(session_id)

    async def stop(self, session_id: str) -> None:
        await self._cancel_session(session_id)

    def invalidate_without_cancellation(self, session_id: str) -> None:
        self.active_session_ids.discard(session_id)

    async def _cancel_session(self, session_id: str) -> None:
        self.active_session_ids.discard(session_id)
        tasks = tuple(self._tasks.get(session_id, ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class GatedExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.observations: list[Observation] = []
        self.cancelled_observation_ids: list[str] = []
        self._started: dict[str, asyncio.Event] = {}
        self._released: dict[str, asyncio.Event] = {}
        self._results: dict[str, ReactionResult] = {}

    def started_for(self, observation_id: str) -> asyncio.Event:
        return self._started.setdefault(observation_id, asyncio.Event())

    def release(self, observation_id: str) -> None:
        self._released.setdefault(observation_id, asyncio.Event()).set()

    def result_for(self, observation_id: str) -> ReactionResult:
        return self._results.setdefault(
            observation_id,
            ReactionResult(published_events=(), validations=()),
        )

    async def react(self, observation: Observation) -> ReactionResult:
        observation_id = observation.observation_id
        self.observations.append(observation)
        self.calls.append(observation_id)
        self.started_for(observation_id).set()
        try:
            await self._released.setdefault(observation_id, asyncio.Event()).wait()
        except asyncio.CancelledError:
            self.cancelled_observation_ids.append(observation_id)
            raise
        return self.result_for(observation_id)


class FlakyExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.success_result = ReactionResult(published_events=(), validations=())

    async def react(self, observation: Observation) -> ReactionResult:
        self.calls.append(observation.observation_id)
        if len(self.calls) == 1:
            raise RuntimeError("temporary reaction failure")
        return self.success_result


def observation(session_id: str, observation_id: str, created_at_ms: int = 100) -> Observation:
    return Observation(
        session_id=session_id,
        observation_id=observation_id,
        created_at_ms=created_at_ms,
    )


def scheduler(
    *,
    executor: GatedExecutor | FlakyExecutor,
    sessions: ManagedSessionTasks,
    clock: FakeClock,
    config: ReactionSchedulerConfig | None = None,
) -> LatestWinsReactionScheduler:
    return LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=sessions,
        clock=clock,
        config=config,
    )


@pytest.mark.asyncio
async def test_latest_submission_replaces_an_observation_that_has_not_started() -> None:
    executor = GatedExecutor()
    instance = scheduler(
        executor=executor,
        sessions=ManagedSessionTasks({"session-1"}),
        clock=FakeClock(100),
    )

    first = await instance.submit(observation("session-1", "first"))
    latest = await instance.submit(observation("session-1", "latest"))

    assert first.done()
    assert first.result() is None
    await asyncio.wait_for(executor.started_for("latest").wait(), timeout=1)
    assert executor.calls == ["latest"]

    executor.release("latest")
    assert await asyncio.wait_for(latest, timeout=1) is executor.result_for("latest")


@pytest.mark.asyncio
async def test_configured_merge_window_coalesces_to_the_latest_observation() -> None:
    executor = GatedExecutor()
    instance = scheduler(
        executor=executor,
        sessions=ManagedSessionTasks({"session-1"}),
        clock=FakeClock(100),
        config=ReactionSchedulerConfig(observation_merge_window_ms=20),
    )

    first_observation = Observation(
        session_id="session-1",
        observation_id="first",
        created_at_ms=100,
        frames=(),
        room_events=(
            RoomEvent(
                event_id="event-1",
                session_id="session-1",
                sequence=1,
                source_type=RoomEventSource.USER_TEXT,
                created_at_ms=100,
                text="first",
            ),
        ),
        trigger_event_ids=("event-1",),
        user_context={"first": "1"},
        target_viewer_id="viewer-1",
    )
    latest_observation = Observation(
        session_id="session-1",
        observation_id="latest",
        created_at_ms=110,
        room_events=(
            RoomEvent(
                event_id="event-2",
                session_id="session-1",
                sequence=2,
                source_type=RoomEventSource.USER_VOICE,
                created_at_ms=110,
                text="second",
            ),
        ),
        trigger_event_ids=("event-2",),
        user_context={"latest": "2"},
        target_persona_id="persona-1",
    )
    first = await instance.submit(first_observation)
    await asyncio.sleep(0)
    latest = await instance.submit(latest_observation)

    await asyncio.wait_for(executor.started_for("latest").wait(), timeout=1)
    assert executor.calls == ["latest"]
    merged = executor.observations[0]
    assert merged.created_at_ms == 100
    assert [event.event_id for event in merged.room_events] == ["event-1", "event-2"]
    assert merged.trigger_event_ids == ("event-1", "event-2")
    assert dict(merged.user_context) == {"first": "1", "latest": "2"}
    assert merged.target_viewer_id is None
    assert merged.target_persona_id is None
    executor.release("latest")
    expected = executor.result_for("latest")
    assert await asyncio.wait_for(first, timeout=1) is expected
    assert await asyncio.wait_for(latest, timeout=1) is expected


@pytest.mark.asyncio
async def test_dynamic_merge_window_is_read_at_each_wave_boundary() -> None:
    executor = GatedExecutor()
    window = 20
    samples: list[int] = []

    async def merge_window(session_id: str) -> int:
        assert session_id == "session-1"
        samples.append(window)
        return window

    instance = LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=ManagedSessionTasks({"session-1"}),
        clock=FakeClock(100),
        merge_window_provider=merge_window,
    )
    first = await instance.submit(
        Observation(
            session_id="session-1",
            observation_id="first",
            created_at_ms=100,
            room_events=(
                RoomEvent(
                    event_id="event-1",
                    session_id="session-1",
                    sequence=1,
                    source_type=RoomEventSource.USER_TEXT,
                    created_at_ms=100,
                    text="first",
                ),
            ),
            trigger_event_ids=("event-1",),
        )
    )
    latest = await instance.submit(
        Observation(
            session_id="session-1",
            observation_id="latest",
            created_at_ms=101,
            room_events=(
                RoomEvent(
                    event_id="event-2",
                    session_id="session-1",
                    sequence=2,
                    source_type=RoomEventSource.USER_VOICE,
                    created_at_ms=101,
                    text="latest",
                ),
            ),
            trigger_event_ids=("event-2",),
        )
    )
    await asyncio.wait_for(executor.started_for("latest").wait(), timeout=1)
    executor.release("latest")
    expected = executor.result_for("latest")
    assert await first is expected
    assert await latest is expected
    assert executor.observations[0].trigger_event_ids == ("event-1", "event-2")

    window = 0
    immediate = await instance.submit(observation("session-1", "immediate"))
    await asyncio.wait_for(executor.started_for("immediate").wait(), timeout=1)
    executor.release("immediate")
    await immediate

    assert 20 in samples
    assert samples[-1] == 0


def test_merge_preserves_ambiguous_target_as_broadcast() -> None:
    merged = LatestWinsReactionScheduler._merge_observations(
        Observation(
            session_id="session-1",
            observation_id="first",
            created_at_ms=1,
            target_ambiguous=True,
        ),
        Observation(
            session_id="session-1",
            observation_id="latest",
            created_at_ms=2,
            target_viewer_id="viewer-1",
        ),
    )

    assert merged.target_ambiguous is True
    assert merged.target_viewer_id is None
    assert merged.target_persona_id is None


@pytest.mark.asyncio
async def test_scheduler_keeps_one_running_and_only_the_latest_pending_observation() -> None:
    executor = GatedExecutor()
    instance = scheduler(
        executor=executor,
        sessions=ManagedSessionTasks({"session-1"}),
        clock=FakeClock(100),
    )

    running = await instance.submit(observation("session-1", "running"))
    await asyncio.wait_for(executor.started_for("running").wait(), timeout=1)
    replaced = await instance.submit(observation("session-1", "replaced"))
    latest = await instance.submit(observation("session-1", "latest"))

    assert replaced.done()
    assert replaced.result() is None
    executor.release("running")
    assert await asyncio.wait_for(running, timeout=1) is executor.result_for("running")

    await asyncio.wait_for(executor.started_for("latest").wait(), timeout=1)
    assert executor.calls == ["running", "latest"]
    executor.release("latest")
    assert await asyncio.wait_for(latest, timeout=1) is executor.result_for("latest")


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle_method", ["pause", "stop"])
async def test_session_lifecycle_cancels_running_reaction(
    lifecycle_method: str,
) -> None:
    executor = GatedExecutor()
    sessions = ManagedSessionTasks({"session-1"})
    instance = scheduler(executor=executor, sessions=sessions, clock=FakeClock(100))

    completion = await instance.submit(observation("session-1", "running"))
    await asyncio.wait_for(executor.started_for("running").wait(), timeout=1)

    await getattr(sessions, lifecycle_method)("session-1")

    assert await asyncio.wait_for(completion, timeout=1) is None
    assert executor.cancelled_observation_ids == ["running"]


@pytest.mark.asyncio
async def test_late_result_from_an_invalidated_session_is_discarded() -> None:
    executor = GatedExecutor()
    sessions = ManagedSessionTasks({"old-session", "new-session"})
    instance = scheduler(executor=executor, sessions=sessions, clock=FakeClock(100))

    old = await instance.submit(observation("old-session", "old"))
    await asyncio.wait_for(executor.started_for("old").wait(), timeout=1)
    sessions.invalidate_without_cancellation("old-session")

    new = await instance.submit(observation("new-session", "new"))
    await asyncio.wait_for(executor.started_for("new").wait(), timeout=1)
    executor.release("old")
    executor.release("new")

    assert await asyncio.wait_for(old, timeout=1) is None
    assert await asyncio.wait_for(new, timeout=1) is executor.result_for("new")


@pytest.mark.asyncio
async def test_scheduler_recovers_after_an_executor_exception() -> None:
    executor = FlakyExecutor()
    instance = scheduler(
        executor=executor,
        sessions=ManagedSessionTasks({"session-1"}),
        clock=FakeClock(100),
    )

    failed = await instance.submit(observation("session-1", "failed"))
    assert await asyncio.wait_for(failed, timeout=1) is None

    recovered = await instance.submit(observation("session-1", "recovered"))
    assert await asyncio.wait_for(recovered, timeout=1) is executor.success_result
    assert executor.calls == ["failed", "recovered"]


@pytest.mark.asyncio
async def test_scheduler_discards_expired_observation_before_execution() -> None:
    executor = GatedExecutor()
    instance = scheduler(
        executor=executor,
        sessions=ManagedSessionTasks({"session-1"}),
        clock=FakeClock(151),
        config=ReactionSchedulerConfig(observation_ttl_ms=50),
    )

    completion = await instance.submit(observation("session-1", "expired", created_at_ms=100))

    assert completion.done()
    assert completion.result() is None
    assert executor.calls == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (ReactionSchedulerConfig, "observation_ttl_ms"),
        (ReactionSchedulerConfig, "max_tracked_sessions"),
        (ReactionSchedulerConfig, "max_pending_observations_per_session"),
        (ReactionSchedulerConfig, "observation_merge_window_ms"),
    ],
)
def test_scheduler_config_requires_bounded_positive_values(
    config: type[ReactionSchedulerConfig],
    message: str,
) -> None:
    values = {
        "observation_ttl_ms": 1,
        "max_tracked_sessions": 1,
        "max_pending_observations_per_session": 1,
    }
    if message == "max_pending_observations_per_session":
        values[message] = 2
    elif message == "observation_merge_window_ms":
        values[message] = -1
    else:
        values[message] = 0

    with pytest.raises(ValueError, match=message):
        config(**values)
