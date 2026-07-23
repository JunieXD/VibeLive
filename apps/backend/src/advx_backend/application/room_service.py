import asyncio
from collections import deque
from collections.abc import Mapping

from advx_backend.application.ports.session import Clock, IdGenerator
from advx_backend.domain.room import RoomEvent, RoomEventSource


class RoomServiceError(RuntimeError):
    pass


class RoomSessionAlreadyActiveError(RoomServiceError):
    def __init__(self, active_session_id: str) -> None:
        self.active_session_id = active_session_id
        super().__init__(f"room session {active_session_id} is already active")


class RoomSessionNotActiveError(RoomServiceError):
    def __init__(self, session_id: str, active_session_id: str | None) -> None:
        self.session_id = session_id
        self.active_session_id = active_session_id
        if active_session_id is None:
            detail = "there is no active room session"
        else:
            detail = f"active room session is {active_session_id}"
        super().__init__(f"room session {session_id} is not active; {detail}")


class RoomService:
    """Owns the bounded public-event buffer for the single active session."""

    def __init__(
        self,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        event_capacity: int,
        event_ttl_ms: int,
    ) -> None:
        if event_capacity < 1:
            raise ValueError("event_capacity must be at least one")
        if event_ttl_ms < 1:
            raise ValueError("event_ttl_ms must be at least one")

        self._clock = clock
        self._id_generator = id_generator
        self._event_capacity = event_capacity
        self._event_ttl_ms = event_ttl_ms
        self._events: deque[RoomEvent] = deque(maxlen=event_capacity)
        self._active_session_id: str | None = None
        self._next_sequence = 1
        self._lock = asyncio.Lock()

    async def start_session(self, session_id: str) -> None:
        self._validate_session_id(session_id)
        async with self._lock:
            if self._active_session_id is not None:
                raise RoomSessionAlreadyActiveError(self._active_session_id)
            self._reset(session_id)

    async def replace_session(self, session_id: str) -> None:
        self._validate_session_id(session_id)
        async with self._lock:
            if self._active_session_id == session_id:
                raise RoomSessionAlreadyActiveError(session_id)
            self._reset(session_id)

    async def stop_session(self, session_id: str) -> None:
        async with self._lock:
            self._require_active(session_id)
            self._events.clear()
            self._active_session_id = None
            self._next_sequence = 1

    async def active_session_id(self) -> str | None:
        async with self._lock:
            return self._active_session_id

    async def require_active_session(self, session_id: str) -> None:
        async with self._lock:
            self._require_active(session_id)

    async def append_event(
        self,
        session_id: str,
        *,
        source_type: RoomEventSource | str,
        source_id: str | None = None,
        text: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> RoomEvent:
        async with self._lock:
            self._require_active(session_id)
            now = self._clock.now_ms()
            self._evict_expired(now)

            event = RoomEvent(
                event_id=self._id_generator.new_id(),
                session_id=session_id,
                sequence=self._next_sequence,
                source_type=RoomEventSource(source_type),
                source_id=source_id,
                created_at_ms=now,
                text=text,
                payload={} if payload is None else payload,
            )
            self._events.append(event)
            self._next_sequence += 1
            return event

    async def read_events(
        self,
        session_id: str,
        *,
        max_count: int | None = None,
    ) -> tuple[RoomEvent, ...]:
        if max_count is not None and max_count < 0:
            raise ValueError("max_count must not be negative")

        async with self._lock:
            self._require_active(session_id)
            self._evict_expired(self._clock.now_ms())
            if max_count == 0:
                return ()
            if max_count is None:
                return tuple(self._events)
            return tuple(self._events)[-max_count:]

    def _reset(self, session_id: str) -> None:
        self._events.clear()
        self._active_session_id = session_id
        self._next_sequence = 1

    def _require_active(self, session_id: str) -> None:
        if self._active_session_id != session_id:
            raise RoomSessionNotActiveError(session_id, self._active_session_id)

    def _evict_expired(self, now_ms: int) -> None:
        retained = (
            event for event in self._events if now_ms - event.created_at_ms < self._event_ttl_ms
        )
        self._events = deque(retained, maxlen=self._event_capacity)

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
