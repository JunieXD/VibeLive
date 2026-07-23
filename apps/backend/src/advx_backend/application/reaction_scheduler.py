"""Bounded, latest-wins scheduling for reactions to observations."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
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

    def __post_init__(self) -> None:
        _require_positive_int("observation_ttl_ms", self.observation_ttl_ms)
        _require_positive_int("max_tracked_sessions", self.max_tracked_sessions)
        if self.max_pending_observations_per_session != 1:
            raise ValueError("max_pending_observations_per_session must be exactly one")


@dataclass(slots=True)
class _ScheduledObservation:
    observation: Observation
    completion: asyncio.Future[ReactionResult | None]


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
    ) -> None:
        self._executor = executor
        self._session_tasks = session_tasks
        self._clock = clock
        self._config = ReactionSchedulerConfig() if config is None else config
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
                self._resolve(schedule.pending.completion, None)
            scheduled = _ScheduledObservation(
                observation=observation,
                completion=completion,
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
                self._resolve(schedule.pending.completion, None)
                schedule.pending = None
            if schedule.running is not None:
                self._resolve(schedule.running.completion, None)
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
                    schedule.pending = None
                    schedule.running = scheduled

                result = await self._execute(scheduled)
                self._resolve(scheduled.completion, result)

                async with self._lock:
                    if self._sessions.get(session_id) is schedule and schedule.running is scheduled:
                        schedule.running = None
        finally:
            async with self._lock:
                if self._sessions.get(session_id) is schedule:
                    if schedule.running is not None:
                        self._resolve(schedule.running.completion, None)
                        schedule.running = None
                    if schedule.pending is not None:
                        self._resolve(schedule.pending.completion, None)
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

    @staticmethod
    def _resolve(
        completion: asyncio.Future[ReactionResult | None],
        result: ReactionResult | None,
    ) -> None:
        if not completion.done():
            completion.set_result(result)

    def _remove_if_idle(self, session_id: str, schedule: _SessionSchedule) -> None:
        if (
            schedule.worker is None
            and schedule.pending is None
            and schedule.running is None
            and self._sessions.get(session_id) is schedule
        ):
            self._sessions.pop(session_id, None)
