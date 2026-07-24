import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from advx_backend.application.ports.persistence import SessionRecordStore
from advx_backend.application.ports.session import (
    Clock,
    IdGenerator,
    SessionResource,
    SessionStatusPublisher,
)
from advx_backend.domain.session import (
    SessionOutcome,
    SessionRecord,
    SessionState,
    SessionStatus,
    can_stop_session,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


class SessionError(RuntimeError):
    pass


class SessionAlreadyActiveError(SessionError):
    def __init__(self, status: SessionStatus) -> None:
        self.status = status
        super().__init__(f"session {status.session_id} is already {status.state}")


class SessionNotFoundError(SessionError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"session {session_id} is not active")


class SessionPersistenceError(SessionError):
    def __init__(self) -> None:
        super().__init__("session persistence is unavailable")


class SessionInitializationError(SessionError):
    def __init__(self) -> None:
        super().__init__("session resources could not be initialized")


class InvalidSessionStateError(SessionError):
    def __init__(
        self,
        *,
        action: str,
        status: SessionStatus,
        allowed_states: set[SessionState],
    ) -> None:
        self.action = action
        self.status = status
        self.allowed_states = frozenset(allowed_states)
        allowed = ", ".join(sorted(state.value for state in allowed_states))
        super().__init__(f"cannot {action} a {status.state} session; expected {allowed}")


class SessionService:
    def __init__(
        self,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        publisher: SessionStatusPublisher,
        session_records: SessionRecordStore | None = None,
        session_resources: SessionResource | None = None,
        app_version: str = "0.1.0",
    ) -> None:
        self._clock = clock
        self._id_generator = id_generator
        self._publisher = publisher
        self._session_records = session_records
        self._session_resources = session_resources
        self._app_version = app_version
        self._lock = asyncio.Lock()
        self._state = SessionState.IDLE
        self._session_id: str | None = None
        self._started_at_ms: int | None = None
        self._updated_at_ms = clock.now_ms()
        self._revision = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._idle = asyncio.Event()
        self._idle.set()

    async def status(self) -> SessionStatus:
        async with self._lock:
            return self._snapshot()

    async def start(self) -> SessionStatus:
        async with self._lock:
            if self._state is not SessionState.IDLE:
                raise SessionAlreadyActiveError(self._snapshot())

            now = self._clock.now_ms()
            session_id = self._id_generator.new_id()
            if self._session_records is not None:
                try:
                    await self._session_records.record_started(
                        SessionRecord(
                            session_id=session_id,
                            started_at_ms=now,
                            app_version=self._app_version,
                        )
                    )
                except Exception as error:
                    logger.exception(
                        "failed to start session record",
                        extra={"session_id": session_id},
                    )
                    raise SessionPersistenceError from error
            if self._session_resources is not None:
                try:
                    await self._session_resources.start_session(session_id)
                except asyncio.CancelledError:
                    await asyncio.shield(self._cleanup_failed_start(session_id, now))
                    raise
                except Exception as error:
                    logger.exception(
                        "failed to initialize session resources",
                        extra={"session_id": session_id},
                    )
                    await asyncio.shield(self._cleanup_failed_start(session_id, now))
                    raise SessionInitializationError from error
            self._idle.clear()
            self._session_id = session_id
            self._started_at_ms = now
            starting = self._transition(SessionState.STARTING, now=now)
            running = self._transition(SessionState.RUNNING)

        await self._publish(starting, running)
        return running

    async def activate_runtime_session(
        self,
        session_id: str,
        *,
        started_at_ms: int,
    ) -> SessionStatus:
        """Adopt a runtime-owned durable Session without inserting it again."""

        if not session_id:
            raise ValueError("session_id must not be empty")
        if started_at_ms < 0:
            raise ValueError("started_at_ms must be nonnegative")
        async with self._lock:
            if self._state is not SessionState.IDLE:
                current = self._snapshot()
                if current.session_id == session_id:
                    return current
                raise SessionAlreadyActiveError(current)

            if self._session_resources is not None:
                try:
                    await self._session_resources.start_session(session_id)
                except asyncio.CancelledError:
                    await asyncio.shield(
                        self._cleanup_adopted_start(session_id)
                    )
                    raise
                except Exception as error:
                    logger.exception(
                        "failed to activate runtime session resources",
                        extra={"session_id": session_id},
                    )
                    await asyncio.shield(
                        self._cleanup_adopted_start(session_id)
                    )
                    raise SessionInitializationError from error
            self._idle.clear()
            self._session_id = session_id
            self._started_at_ms = started_at_ms
            starting = self._transition(
                SessionState.STARTING,
                now=started_at_ms,
            )
            running = self._transition(SessionState.RUNNING)

        await self._publish(starting, running)
        return running

    async def abandon_runtime_session(self, session_id: str) -> SessionStatus:
        """Compensate a failed runtime start without touching durable records."""

        async with self._lock:
            current = self._require_session(session_id)
            if not can_stop_session(current.state):
                raise InvalidSessionStateError(
                    action="abandon",
                    status=current,
                    allowed_states={
                        SessionState.STARTING,
                        SessionState.RUNNING,
                        SessionState.PAUSED,
                        SessionState.ERROR,
                    },
                )
            stopping = self._transition(SessionState.STOPPING)
            tasks = self._detach_tasks()

        await self._cancel_tasks(tasks)
        if self._session_resources is not None:
            try:
                await self._session_resources.stop_session(session_id)
            except Exception as error:
                logger.warning(
                    "failed to clean up abandoned runtime session resources",
                    extra={
                        "session_id": session_id,
                        "error_type": type(error).__name__,
                    },
                )
        async with self._lock:
            if (
                self._session_id != session_id
                or self._state is not SessionState.STOPPING
            ):
                raise SessionNotFoundError(session_id)
            self._session_id = None
            self._started_at_ms = None
            idle = self._transition(SessionState.IDLE)
            self._idle.set()
        await self._publish(stopping, idle)
        return idle

    async def pause(self, session_id: str) -> SessionStatus:
        async with self._lock:
            current = self._require_session(session_id)
            if current.state is not SessionState.RUNNING:
                raise InvalidSessionStateError(
                    action="pause",
                    status=current,
                    allowed_states={SessionState.RUNNING},
                )
            paused = self._transition(SessionState.PAUSED)
            tasks = self._detach_tasks()

        try:
            await self._publisher.publish_session_status(paused)
        finally:
            await asyncio.shield(self._cancel_tasks(tasks))
        return paused

    async def resume(self, session_id: str) -> SessionStatus:
        async with self._lock:
            current = self._require_session(session_id)
            if current.state is not SessionState.PAUSED:
                raise InvalidSessionStateError(
                    action="resume",
                    status=current,
                    allowed_states={SessionState.PAUSED},
                )
            running = self._transition(SessionState.RUNNING)

        await self._publisher.publish_session_status(running)
        return running

    async def stop(self, session_id: str) -> SessionStatus:
        async with self._lock:
            current = self._require_session(session_id)
            if not can_stop_session(current.state):
                raise InvalidSessionStateError(
                    action="stop",
                    status=current,
                    allowed_states={
                        SessionState.STARTING,
                        SessionState.RUNNING,
                        SessionState.PAUSED,
                        SessionState.ERROR,
                    },
                )
            stopping = self._transition(SessionState.STOPPING)
            tasks = self._detach_tasks()
            outcome = (
                SessionOutcome.ERROR
                if current.state is SessionState.ERROR
                else SessionOutcome.COMPLETED
            )

        logger.info(
            "session.stop.requested",
            extra={"session_id": session_id, "previous_state": current.state.value},
        )

        try:
            await self._publisher.publish_session_status(stopping)
        finally:
            idle = await asyncio.shield(self._complete_stop(session_id, tasks, outcome=outcome))
        logger.info(
            "session.stop.completed",
            extra={"session_id": session_id, "outcome": outcome.value},
        )
        return idle

    async def mark_error(self, session_id: str) -> SessionStatus:
        async with self._lock:
            current = self._require_session(session_id)
            if current.state in {SessionState.STOPPING, SessionState.IDLE}:
                raise InvalidSessionStateError(
                    action="mark as error",
                    status=current,
                    allowed_states={
                        SessionState.STARTING,
                        SessionState.RUNNING,
                        SessionState.PAUSED,
                        SessionState.ERROR,
                    },
                )
            if current.state is SessionState.ERROR:
                return current
            failed = self._transition(SessionState.ERROR)
            tasks = self._detach_tasks()

        logger.warning(
            "session.marked_error",
            extra={"session_id": session_id, "previous_state": current.state.value},
        )

        try:
            await self._publisher.publish_session_status(failed)
        finally:
            await asyncio.shield(self._cancel_tasks(tasks))
        return failed

    async def start_task(
        self,
        session_id: str,
        factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        async with self._lock:
            current = self._require_session(session_id)
            if current.state is not SessionState.RUNNING:
                raise InvalidSessionStateError(
                    action="start work for",
                    status=current,
                    allowed_states={SessionState.RUNNING},
                )
            task = asyncio.create_task(factory(), name=name)
            self._tasks.add(task)

        task.add_done_callback(self._on_task_done)
        return task

    async def accepts_results(self, session_id: str) -> bool:
        async with self._lock:
            return self._session_id == session_id and self._state is SessionState.RUNNING

    async def shutdown(self) -> None:
        async with self._lock:
            session_id = self._session_id
            state = self._state
        if state is SessionState.STOPPING:
            await self._idle.wait()
            return
        if session_id is not None and can_stop_session(state):
            await self.stop(session_id)

    def _require_session(self, session_id: str) -> SessionStatus:
        if self._session_id != session_id:
            raise SessionNotFoundError(session_id)
        return self._snapshot()

    def _transition(self, state: SessionState, *, now: int | None = None) -> SessionStatus:
        self._state = state
        self._updated_at_ms = self._clock.now_ms() if now is None else now
        self._revision += 1
        return self._snapshot()

    def _snapshot(self) -> SessionStatus:
        return SessionStatus(
            session_id=self._session_id,
            state=self._state,
            started_at_ms=self._started_at_ms,
            updated_at_ms=self._updated_at_ms,
            revision=self._revision,
        )

    def _detach_tasks(self) -> tuple[asyncio.Task[Any], ...]:
        tasks = tuple(self._tasks)
        self._tasks.clear()
        return tasks

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "session task failed",
                    extra={
                        "task_name": task.get_name(),
                        "error_type": type(error).__name__,
                    },
                )

    async def _publish(self, *statuses: SessionStatus) -> None:
        for status in statuses:
            await self._publisher.publish_session_status(status)

    async def _complete_stop(
        self,
        session_id: str,
        tasks: tuple[asyncio.Task[Any], ...],
        *,
        outcome: SessionOutcome,
    ) -> SessionStatus:
        await self._cancel_tasks(tasks)
        if self._session_resources is not None:
            try:
                await self._session_resources.stop_session(session_id)
            except Exception as error:
                logger.warning(
                    "failed to clean up session resources",
                    extra={
                        "session_id": session_id,
                        "error_type": type(error).__name__,
                    },
                )
        ended_at_ms = max(self._started_at_ms or 0, self._clock.now_ms())
        if self._session_records is not None:
            try:
                await self._session_records.record_finished(
                    session_id,
                    ended_at_ms=ended_at_ms,
                    outcome=outcome,
                )
            except Exception:
                logger.exception(
                    "failed to finish session record",
                    extra={"session_id": session_id},
                )
        async with self._lock:
            if self._session_id != session_id or self._state is not SessionState.STOPPING:
                raise SessionNotFoundError(session_id)
            self._session_id = None
            self._started_at_ms = None
            idle = self._transition(SessionState.IDLE)
            self._idle.set()
        await self._publisher.publish_session_status(idle)
        return idle

    async def _cleanup_failed_start(self, session_id: str, started_at_ms: int) -> None:
        if self._session_resources is not None:
            try:
                await self._session_resources.stop_session(session_id)
            except Exception as error:
                logger.warning(
                    "failed to clean up partially initialized session resources",
                    extra={
                        "session_id": session_id,
                        "error_type": type(error).__name__,
                    },
                )
        if self._session_records is not None:
            try:
                await self._session_records.record_finished(
                    session_id,
                    ended_at_ms=max(started_at_ms, self._clock.now_ms()),
                    outcome=SessionOutcome.ERROR,
                )
            except Exception as error:
                logger.warning(
                    "failed to close session record after initialization failure",
                    extra={
                        "session_id": session_id,
                        "error_type": type(error).__name__,
                    },
                )

    async def _cleanup_adopted_start(self, session_id: str) -> None:
        if self._session_resources is None:
            return
        try:
            await self._session_resources.stop_session(session_id)
        except Exception as error:
            logger.warning(
                "failed to clean up adopted runtime session resources",
                extra={
                    "session_id": session_id,
                    "error_type": type(error).__name__,
                },
            )

    @staticmethod
    async def _cancel_tasks(tasks: tuple[asyncio.Task[Any], ...]) -> None:
        current = asyncio.current_task()
        joinable = tuple(task for task in tasks if task is not current)
        for task in joinable:
            if not task.done():
                task.cancel()
        if joinable:
            await asyncio.gather(*joinable, return_exceptions=True)
