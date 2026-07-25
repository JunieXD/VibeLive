"""Ordered scheduling for reactions to user observations."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from advx_backend.application.ports.generation import SessionTaskScope
from advx_backend.application.ports.session import Clock
from advx_backend.application.reaction_service import ReactionResult
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEventSource

__all__ = [
    "LatestWinsReactionScheduler",
    "ReactionPreparationError",
    "ReactionExecutor",
    "ReactionSchedulerConfig",
]

logger = logging.getLogger(__name__)


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


class ReactionExecutor(Protocol):
    """The narrow dependency needed to run an observation reaction."""

    async def react(self, observation: Observation) -> ReactionResult: ...


class ReactionPreparationError(RuntimeError):
    """A failure before any Viewer model request has been started.

    These failures are safe to retry once because no model output or room-side
    effect can have been produced yet.
    """


class ReactionFailureReporter(Protocol):
    async def __call__(self, observation: Observation, error: Exception) -> None: ...


@dataclass(frozen=True, slots=True)
class ReactionSchedulerConfig:
    """Resource limits for :class:`LatestWinsReactionScheduler`."""

    observation_ttl_ms: int = 900_000
    max_tracked_sessions: int = 32
    max_pending_observations_per_session: int = 1_024
    observation_merge_window_ms: int = 0
    preparation_retry_count: int = 1
    preparation_retry_backoff_ms: int = 50

    def __post_init__(self) -> None:
        _require_positive_int("observation_ttl_ms", self.observation_ttl_ms)
        _require_positive_int("max_tracked_sessions", self.max_tracked_sessions)
        _require_positive_int(
            "max_pending_observations_per_session",
            self.max_pending_observations_per_session,
        )
        if (
            isinstance(self.observation_merge_window_ms, bool)
            or not isinstance(self.observation_merge_window_ms, int)
            or self.observation_merge_window_ms < 0
        ):
            raise ValueError("observation_merge_window_ms must be a non-negative integer")
        if (
            isinstance(self.preparation_retry_count, bool)
            or not isinstance(self.preparation_retry_count, int)
            or self.preparation_retry_count < 0
        ):
            raise ValueError("preparation_retry_count must be a non-negative integer")
        if (
            isinstance(self.preparation_retry_backoff_ms, bool)
            or not isinstance(self.preparation_retry_backoff_ms, int)
            or self.preparation_retry_backoff_ms < 0
        ):
            raise ValueError("preparation_retry_backoff_ms must be a non-negative integer")


@dataclass(slots=True)
class _ScheduledObservation:
    observation: Observation
    completions: list[asyncio.Future[ReactionResult | None]]
    merge_window_ms: int
    merge_deadline_ms: int
    superseded: bool = False
    execution_task: asyncio.Task[ReactionResult | None] | None = None


@dataclass(slots=True)
class _SessionSchedule:
    worker: asyncio.Task[None] | None = None
    pending: _ScheduledObservation | None = None
    running: _ScheduledObservation | None = None


class LatestWinsReactionScheduler:
    """Run one reaction plus one priority-aware latest pending item per Session."""

    def __init__(
        self,
        *,
        executor: ReactionExecutor,
        session_tasks: SessionTaskScope,
        clock: Clock,
        config: ReactionSchedulerConfig | None = None,
        merge_window_provider: Callable[[str], Awaitable[int]] | None = None,
        failure_reporter: ReactionFailureReporter | None = None,
    ) -> None:
        self._executor = executor
        self._session_tasks = session_tasks
        self._clock = clock
        self._config = ReactionSchedulerConfig() if config is None else config
        self._merge_window_provider = merge_window_provider
        self._failure_reporter = failure_reporter
        self._lock = asyncio.Lock()
        self._sessions: OrderedDict[str, _SessionSchedule] = OrderedDict()

    async def submit(
        self,
        observation: Observation,
    ) -> asyncio.Future[ReactionResult | None]:
        """Queue an observation and return its completion future."""

        completion: asyncio.Future[ReactionResult | None] = (
            asyncio.get_running_loop().create_future()
        )
        if self._is_expired(observation):
            completion.set_result(None)
            return completion
        merge_window_ms = await self._merge_window_ms(observation.session_id)

        async with self._lock:
            schedule = self._sessions.get(observation.session_id)
            if schedule is None:
                if len(self._sessions) >= self._config.max_tracked_sessions:
                    completion.set_result(None)
                    return completion
                schedule = _SessionSchedule()
                self._sessions[observation.session_id] = schedule
            else:
                self._sessions.move_to_end(observation.session_id)

            priority = self._priority(observation)
            queue_behind_running = False
            running_priority = (
                None
                if schedule.running is None
                else self._priority(schedule.running.observation)
            )
            running_yields_to_observation = (
                schedule.running is not None
                and self._screen_trigger_yields_to(
                    schedule.running.observation,
                    observation,
                )
            )
            if (
                schedule.running is not None
                and self._screen_trigger_yields_to(
                    observation,
                    schedule.running.observation,
                )
            ):
                completion.set_result(None)
                return completion
            if (
                schedule.running is not None
                and not running_yields_to_observation
                and running_priority is not None
                and priority < running_priority
            ):
                pending_priority = (
                    None
                    if schedule.pending is None
                    else self._priority(schedule.pending.observation)
                )
                if self._is_ambient_trigger(observation) or (
                    pending_priority is not None and priority > pending_priority
                ):
                    queue_behind_running = True
                else:
                    completion.set_result(None)
                    return completion
            elif (
                schedule.running is not None
                and not running_yields_to_observation
                and running_priority is not None
                and priority == running_priority
                and priority < 3
            ):
                queue_behind_running = True
            if schedule.running is not None and not queue_behind_running:
                schedule.running.superseded = True
                if schedule.running.execution_task is not None:
                    schedule.running.execution_task.cancel()
            if schedule.pending is not None:
                pending_priority = self._priority(schedule.pending.observation)
                if self._screen_trigger_yields_to(observation, schedule.pending.observation):
                    completion.set_result(None)
                    return completion
                if self._screen_trigger_yields_to(schedule.pending.observation, observation):
                    self._resolve_all(schedule.pending.completions, None)
                elif priority < pending_priority:
                    completion.set_result(None)
                    return completion
                elif priority > pending_priority:
                    self._resolve_all(schedule.pending.completions, None)
                elif (
                    schedule.pending.merge_window_ms
                    and self._within_merge_window(
                        schedule.pending,
                        observation,
                    )
                    and self._can_merge_pending(
                        schedule.pending.observation,
                        observation,
                    )
                ):
                    if (
                        len(schedule.pending.completions)
                        >= self._config.max_pending_observations_per_session
                    ):
                        self._resolve(schedule.pending.completions.pop(0), None)
                    schedule.pending.observation = self._merge_observations(
                        schedule.pending.observation,
                        observation,
                    )
                    schedule.pending.completions.append(completion)
                    return completion
                else:
                    self._resolve_all(schedule.pending.completions, None)
            scheduled = _ScheduledObservation(
                observation=observation,
                completions=[completion],
                merge_window_ms=merge_window_ms,
                merge_deadline_ms=self._clock.now_ms() + merge_window_ms,
            )
            schedule.pending = scheduled
            if schedule.worker is None:
                try:
                    schedule.worker = await self._session_tasks.start_task(
                        observation.session_id,
                        lambda: self._run_session(observation.session_id, schedule),
                        name=f"reaction-scheduler:{observation.session_id}",
                    )
                except asyncio.CancelledError:
                    if schedule.pending is scheduled:
                        schedule.pending = None
                    self._resolve(completion, None)
                    self._remove_if_idle(observation.session_id, schedule)
                    raise
                except Exception as error:
                    logger.info(
                        "reaction scheduler rejected observation",
                        extra={
                            "session_id": observation.session_id,
                            "observation_id": observation.observation_id,
                            "error_type": type(error).__name__,
                        },
                    )
                    if schedule.pending is scheduled:
                        schedule.pending = None
                    self._resolve(completion, None)
                    self._remove_if_idle(observation.session_id, schedule)

        return completion

    async def schedule(
        self,
        observation: Observation,
    ) -> asyncio.Future[ReactionResult | None]:
        """Alias for :meth:`submit` for callers that name the operation schedule."""

        return await self.submit(observation)

    async def enqueue(
        self,
        observation: Observation,
    ) -> asyncio.Future[ReactionResult | None]:
        """Alias for :meth:`submit` for queue-oriented callers."""

        return await self.submit(observation)

    async def pause_session(self, session_id: str) -> None:
        """Explicitly cancel scheduled work when a lifecycle adapter pauses a session."""

        await self.cancel_session(session_id)

    async def stop_session(self, session_id: str) -> None:
        """Explicitly cancel scheduled work when a lifecycle adapter stops a session."""

        await self.cancel_session(session_id)

    async def cancel_session(self, session_id: str) -> None:
        """Cancel running work and discard any pending or late result for a session."""

        async with self._lock:
            schedule = self._sessions.pop(session_id, None)
            if schedule is None:
                return

            for scheduled in (schedule.pending, schedule.running):
                if scheduled is not None:
                    self._resolve_all(scheduled.completions, None)
            schedule.pending = None
            schedule.running = None
            worker = schedule.worker
            schedule.worker = None

        current_task = asyncio.current_task()
        if worker is not None and worker is not current_task and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def wait_for_idle(self, session_id: str | None = None) -> None:
        """Wait for currently scheduled work; primarily useful to lifecycle adapters."""

        while True:
            async with self._lock:
                if session_id is None:
                    workers = tuple(
                        schedule.worker
                        for schedule in self._sessions.values()
                        if schedule.worker is not None
                    )
                else:
                    schedule = self._sessions.get(session_id)
                    workers = (
                        () if schedule is None or schedule.worker is None else (schedule.worker,)
                    )
            if not workers:
                return
            await asyncio.gather(*workers, return_exceptions=True)

    async def _run_session(
        self,
        session_id: str,
        schedule: _SessionSchedule,
    ) -> None:
        try:
            while True:
                async with self._lock:
                    if self._sessions.get(session_id) is not schedule:
                        return
                    scheduled = schedule.pending
                    if scheduled is None:
                        schedule.worker = None
                        self._remove_if_idle(session_id, schedule)
                        return
                remaining_merge_ms = scheduled.merge_deadline_ms - self._clock.now_ms()
                if remaining_merge_ms > 0:
                    await asyncio.sleep(remaining_merge_ms / 1_000)
                async with self._lock:
                    if self._sessions.get(session_id) is not schedule:
                        return
                    if schedule.pending is not scheduled:
                        continue
                    schedule.pending = None
                    schedule.running = scheduled
                scheduled.execution_task = asyncio.create_task(
                    self._execute(scheduled),
                    name=f"reaction-execute:{session_id}:{scheduled.observation.observation_id}",
                )
                try:
                    result = await scheduled.execution_task
                except asyncio.CancelledError:
                    if not scheduled.superseded:
                        raise
                    result = None
                finally:
                    scheduled.execution_task = None
                async with self._lock:
                    still_current = (
                        self._sessions.get(session_id) is schedule
                        and schedule.running is scheduled
                        and not scheduled.superseded
                    )
                    if self._sessions.get(session_id) is schedule:
                        schedule.running = None
                self._resolve_all(scheduled.completions, result if still_current else None)
        finally:
            async with self._lock:
                if self._sessions.get(session_id) is schedule:
                    for scheduled in (schedule.running, schedule.pending):
                        if scheduled is not None:
                            self._resolve_all(scheduled.completions, None)
                    schedule.running = None
                    schedule.pending = None
                    schedule.worker = None
                    self._remove_if_idle(session_id, schedule)

    async def _execute(
        self,
        scheduled: _ScheduledObservation,
    ) -> ReactionResult | None:
        observation = scheduled.observation
        if self._is_expired(observation):
            return None
        if not await self._session_tasks.accepts_results(observation.session_id):
            return None

        attempts = self._config.preparation_retry_count + 1
        for attempt in range(attempts):
            try:
                result = await self._executor.react(observation)
                break
            except asyncio.CancelledError:
                raise
            except ReactionPreparationError as error:
                if attempt + 1 < attempts:
                    logger.warning(
                        "reaction preparation failed; retrying",
                        exc_info=True,
                        extra={
                            "session_id": observation.session_id,
                            "observation_id": observation.observation_id,
                            "attempt": attempt + 1,
                        },
                    )
                    if self._config.preparation_retry_backoff_ms:
                        await asyncio.sleep(self._config.preparation_retry_backoff_ms / 1_000)
                    continue
                await self._report_failure(observation, error)
                logger.exception(
                    "reaction preparation failed after retry",
                    extra={
                        "session_id": observation.session_id,
                        "observation_id": observation.observation_id,
                        "attempts": attempts,
                    },
                )
                return None
            except Exception as error:
                await self._report_failure(observation, error)
                logger.exception(
                    "reaction execution failed",
                    extra={
                        "session_id": observation.session_id,
                        "observation_id": observation.observation_id,
                        "error_type": type(error).__name__,
                    },
                )
                return None

        if self._is_expired(observation):
            return None
        if not await self._session_tasks.accepts_results(observation.session_id):
            return None
        return result

    async def _report_failure(
        self,
        observation: Observation,
        error: Exception,
    ) -> None:
        if self._failure_reporter is None:
            return
        try:
            await self._failure_reporter(observation, error)
        except Exception:
            logger.exception(
                "reaction failure reporting failed",
                extra={
                    "session_id": observation.session_id,
                    "observation_id": observation.observation_id,
                },
            )

    def _is_expired(self, observation: Observation) -> bool:
        return self._clock.now_ms() >= observation.created_at_ms + self._config.observation_ttl_ms

    async def _merge_window_ms(self, session_id: str) -> int:
        if self._merge_window_provider is None:
            return self._config.observation_merge_window_ms
        value = await self._merge_window_provider(session_id)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("dynamic observation merge window must be non-negative")
        return value

    @classmethod
    def _priority(cls, observation: Observation) -> int:
        trigger_ids = set(observation.trigger_event_ids)
        trigger_sources = cls._trigger_sources(observation)
        if trigger_sources & {RoomEventSource.USER_TEXT, RoomEventSource.USER_VOICE}:
            return 3
        if (
            observation.trigger_frame_ids
            or RoomEventSource.SCREEN_OBSERVATION in trigger_sources
            or any(
                event.event_id in trigger_ids
                and event.source_type is RoomEventSource.SYSTEM_EVENT
                and event.payload.get("event") == "system_audio_transcript"
                for event in observation.room_events
            )
        ):
            return 2
        return 1

    @classmethod
    def _screen_trigger_yields_to(
        cls,
        candidate: Observation,
        existing: Observation,
    ) -> bool:
        return cls._is_screen_only_trigger(candidate) and not cls._is_screen_only_trigger(
            existing
        )

    @staticmethod
    def _trigger_sources(observation: Observation) -> set[RoomEventSource]:
        trigger_ids = set(observation.trigger_event_ids)
        return {
            event.source_type
            for event in observation.room_events
            if event.event_id in trigger_ids
        }

    @classmethod
    def _is_screen_only_trigger(cls, observation: Observation) -> bool:
        trigger_sources = cls._trigger_sources(observation)
        return (
            bool(observation.trigger_frame_ids)
            or RoomEventSource.SCREEN_OBSERVATION in trigger_sources
        ) and not trigger_sources - {RoomEventSource.SCREEN_OBSERVATION} and (
            observation.user_context.get("ambient") != "true"
        )

    @staticmethod
    def _is_ambient_trigger(observation: Observation) -> bool:
        return observation.user_context.get("ambient") == "true"

    @classmethod
    def _can_merge_pending(
        cls,
        first: Observation,
        latest: Observation,
    ) -> bool:
        if cls._priority(first) != cls._priority(latest):
            return False
        if cls._priority(first) == 3:
            return True
        return (
            cls._is_ambient_trigger(first) == cls._is_ambient_trigger(latest)
            and cls._is_screen_only_trigger(first) == cls._is_screen_only_trigger(latest)
        )

    def _within_merge_window(
        self,
        pending: _ScheduledObservation,
        latest: Observation,
    ) -> bool:
        return (
            self._clock.now_ms() < pending.merge_deadline_ms
            and latest.created_at_ms
            < pending.observation.created_at_ms + pending.merge_window_ms
        )

    @staticmethod
    def _resolve(
        completion: asyncio.Future[ReactionResult | None],
        result: ReactionResult | None,
    ) -> None:
        if not completion.done():
            completion.set_result(result)

    @classmethod
    def _resolve_all(
        cls,
        completions: list[asyncio.Future[ReactionResult | None]],
        result: ReactionResult | None,
    ) -> None:
        for completion in completions:
            cls._resolve(completion, result)

    @staticmethod
    def _merge_observations(first: Observation, latest: Observation) -> Observation:
        if first.session_id != latest.session_id:
            raise ValueError("cannot merge observations from different sessions")
        frames = {frame.frame_id: frame for frame in (*first.frames, *latest.frames)}
        events = {event.event_id: event for event in (*first.room_events, *latest.room_events)}
        target_viewer_id, target_persona_id, target_ambiguous = (
            LatestWinsReactionScheduler._merge_targets(first, latest)
        )
        return Observation(
            session_id=first.session_id,
            observation_id=latest.observation_id,
            created_at_ms=min(first.created_at_ms, latest.created_at_ms),
            frames=tuple(
                sorted(frames.values(), key=lambda item: (item.created_at_ms, item.frame_id))
            ),
            room_events=tuple(
                sorted(events.values(), key=lambda item: (item.sequence, item.event_id))
            ),
            trigger_event_ids=tuple(
                dict.fromkeys((*first.trigger_event_ids, *latest.trigger_event_ids))
            ),
            trigger_frame_ids=tuple(
                dict.fromkeys((*first.trigger_frame_ids, *latest.trigger_frame_ids))
            ),
            user_context={**first.user_context, **latest.user_context},
            target_viewer_id=target_viewer_id,
            target_persona_id=target_persona_id,
            target_ambiguous=target_ambiguous,
        )

    @staticmethod
    def _merge_targets(
        first: Observation,
        latest: Observation,
    ) -> tuple[str | None, str | None, bool]:
        if first.target_ambiguous or latest.target_ambiguous:
            return None, None, True
        targets = {
            ("viewer", target)
            for target in (first.target_viewer_id, latest.target_viewer_id)
            if target is not None
        }
        targets.update(
            ("persona", target)
            for target in (first.target_persona_id, latest.target_persona_id)
            if target is not None
        )
        for observation in (first, latest):
            trigger_ids = set(observation.trigger_event_ids)
            if not trigger_ids:
                continue
            for event in observation.room_events:
                if trigger_ids and event.event_id not in trigger_ids:
                    continue
                viewer = event.payload.get("target_viewer_id")
                persona = event.payload.get("target_persona_id")
                if isinstance(viewer, str) and viewer:
                    targets.add(("viewer", viewer))
                if isinstance(persona, str) and persona:
                    targets.add(("persona", persona))
        if len(targets) != 1:
            return None, None, len(targets) > 1
        kind, target = next(iter(targets))
        return (target, None, False) if kind == "viewer" else (None, target, False)

    def _remove_if_idle(self, session_id: str, schedule: _SessionSchedule) -> None:
        if (
            schedule.worker is None
            and schedule.pending is None
            and schedule.running is None
            and self._sessions.get(session_id) is schedule
        ):
            self._sessions.pop(session_id, None)
