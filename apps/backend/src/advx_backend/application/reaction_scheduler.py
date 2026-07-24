"""Bounded, latest-wins scheduling for reactions to observations."""

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

__all__ = [
    "LatestWinsReactionScheduler",
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


@dataclass(frozen=True, slots=True)
class ReactionSchedulerConfig:
    """Resource limits for :class:`LatestWinsReactionScheduler`."""

    observation_ttl_ms: int = 30_000
    max_tracked_sessions: int = 32
    max_pending_observations_per_session: int = 1
    observation_merge_window_ms: int = 0

    def __post_init__(self) -> None:
        _require_positive_int("observation_ttl_ms", self.observation_ttl_ms)
        _require_positive_int("max_tracked_sessions", self.max_tracked_sessions)
        if self.max_pending_observations_per_session != 1:
            raise ValueError("max_pending_observations_per_session must be exactly one")
        if (
            isinstance(self.observation_merge_window_ms, bool)
            or not isinstance(self.observation_merge_window_ms, int)
            or self.observation_merge_window_ms < 0
        ):
            raise ValueError("observation_merge_window_ms must be a non-negative integer")


@dataclass(slots=True)
class _ScheduledObservation:
    observation: Observation
    completions: list[asyncio.Future[ReactionResult | None]]
    merge_window_ms: int


@dataclass(slots=True)
class _SessionSchedule:
    worker: asyncio.Task[None] | None = None
    pending: _ScheduledObservation | None = None
    running: _ScheduledObservation | None = None


class LatestWinsReactionScheduler:
    """Run at most one reaction plus one replaceable pending observation per session.

    A newly submitted observation replaces the pending item if the worker has
    not started it.  Once execution starts, the scheduler lets it finish, but
    validates both session liveness and observation TTL before exposing its
    result.  The worker is registered with ``SessionTaskScope`` so the existing
    session pause and stop paths cancel it without any SessionService changes.
    """

    def __init__(
        self,
        *,
        executor: ReactionExecutor,
        session_tasks: SessionTaskScope,
        clock: Clock,
        config: ReactionSchedulerConfig | None = None,
        merge_window_provider: Callable[[str], Awaitable[int]] | None = None,
    ) -> None:
        self._executor = executor
        self._session_tasks = session_tasks
        self._clock = clock
        self._config = ReactionSchedulerConfig() if config is None else config
        self._merge_window_provider = merge_window_provider
        self._lock = asyncio.Lock()
        self._sessions: OrderedDict[str, _SessionSchedule] = OrderedDict()

    async def submit(
        self,
        observation: Observation,
    ) -> asyncio.Future[ReactionResult | None]:
        """Queue an observation and return its completion future.

        A completion resolves to ``None`` when the observation is superseded,
        expired, cancelled with its session, rejected by the session scope, or
        its reaction fails.  Errors are contained so a later observation can
        still run on the same scheduler.
        """

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

            if schedule.pending is not None:
                if schedule.pending.merge_window_ms:
                    schedule.pending.observation = self._merge_observations(
                        schedule.pending.observation,
                        observation,
                    )
                    schedule.pending.completions.append(completion)
                    return completion
                self._resolve_all(schedule.pending.completions, None)
            scheduled = _ScheduledObservation(
                observation=observation,
                completions=[completion],
                merge_window_ms=merge_window_ms,
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

            if schedule.pending is not None:
                self._resolve_all(schedule.pending.completions, None)
                schedule.pending = None
            if schedule.running is not None:
                self._resolve_all(schedule.running.completions, None)
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

    async def _run_session(self, session_id: str, schedule: _SessionSchedule) -> None:
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
                if scheduled.merge_window_ms:
                    await asyncio.sleep(scheduled.merge_window_ms / 1_000)
                async with self._lock:
                    if self._sessions.get(session_id) is not schedule:
                        return
                    if schedule.pending is not scheduled:
                        continue
                    schedule.pending = None
                    schedule.running = scheduled

                result = await self._execute(scheduled)
                self._resolve_all(scheduled.completions, result)

                async with self._lock:
                    if self._sessions.get(session_id) is schedule and schedule.running is scheduled:
                        schedule.running = None
        finally:
            async with self._lock:
                if self._sessions.get(session_id) is schedule:
                    if schedule.running is not None:
                        self._resolve_all(schedule.running.completions, None)
                        schedule.running = None
                    if schedule.pending is not None:
                        self._resolve_all(schedule.pending.completions, None)
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

        try:
            result = await self._executor.react(observation)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
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

    def _is_expired(self, observation: Observation) -> bool:
        return self._clock.now_ms() >= observation.created_at_ms + self._config.observation_ttl_ms

    async def _merge_window_ms(self, session_id: str) -> int:
        if self._merge_window_provider is None:
            return self._config.observation_merge_window_ms
        value = await self._merge_window_provider(session_id)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("dynamic observation merge window must be non-negative")
        return value

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
        frames = {
            frame.frame_id: frame
            for frame in (*first.frames, *latest.frames)
        }
        events = {
            event.event_id: event
            for event in (*first.room_events, *latest.room_events)
        }
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
        return (
            (target, None, False)
            if kind == "viewer"
            else (None, target, False)
        )

    def _remove_if_idle(self, session_id: str, schedule: _SessionSchedule) -> None:
        if (
            schedule.worker is None
            and schedule.pending is None
            and schedule.running is None
            and self._sessions.get(session_id) is schedule
        ):
            self._sessions.pop(session_id, None)
