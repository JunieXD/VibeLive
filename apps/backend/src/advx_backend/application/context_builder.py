import asyncio
from collections import deque
from collections.abc import Mapping

from advx_backend.application.ports.session import Clock, IdGenerator
from advx_backend.application.room_service import RoomService, RoomSessionNotActiveError
from advx_backend.domain.observation import FrameRef, Observation


class ContextBuilder:
    """Builds immutable observations from bounded references and room events."""

    def __init__(
        self,
        *,
        room_service: RoomService,
        clock: Clock,
        id_generator: IdGenerator,
        frame_capacity: int,
        frame_ttl_ms: int,
        max_frames_per_observation: int,
        max_events_per_observation: int,
    ) -> None:
        if frame_capacity < 1:
            raise ValueError("frame_capacity must be at least one")
        if frame_ttl_ms < 1:
            raise ValueError("frame_ttl_ms must be at least one")
        if max_frames_per_observation < 0:
            raise ValueError("max_frames_per_observation must not be negative")
        if max_events_per_observation < 0:
            raise ValueError("max_events_per_observation must not be negative")

        self._room_service = room_service
        self._clock = clock
        self._id_generator = id_generator
        self._frame_capacity = frame_capacity
        self._frame_ttl_ms = frame_ttl_ms
        self._max_frames_per_observation = max_frames_per_observation
        self._max_events_per_observation = max_events_per_observation
        self._frames: deque[FrameRef] = deque(maxlen=frame_capacity)
        self._active_session_id: str | None = None
        self._lock = asyncio.Lock()

    async def start_session(self, session_id: str) -> None:
        await self._room_service.start_session(session_id)
        async with self._lock:
            self._reset(session_id)

    async def replace_session(self, session_id: str) -> None:
        await self._room_service.replace_session(session_id)
        async with self._lock:
            self._reset(session_id)

    async def stop_session(self, session_id: str) -> None:
        try:
            await self._room_service.stop_session(session_id)
        finally:
            async with self._lock:
                if self._active_session_id == session_id:
                    self._frames.clear()
                    self._active_session_id = None

    async def append_frame(
        self,
        session_id: str,
        *,
        mime_type: str,
        data_ref: str,
    ) -> FrameRef:
        frame = FrameRef(
            frame_id=self._id_generator.new_id(),
            created_at_ms=self._clock.now_ms(),
            mime_type=mime_type,
            data_ref=data_ref,
        )
        return await self.append_frame_ref(session_id, frame)

    async def append_frame_ref(self, session_id: str, frame: FrameRef) -> FrameRef:
        await self._room_service.require_active_session(session_id)
        async with self._lock:
            self._require_active_session(session_id)
            self._evict_expired(self._clock.now_ms())
            self._frames.append(frame)

        try:
            await self._room_service.require_active_session(session_id)
        except RoomSessionNotActiveError:
            async with self._lock:
                if self._active_session_id == session_id:
                    try:
                        self._frames.remove(frame)
                    except ValueError:
                        pass
            raise
        return frame

    async def build(
        self,
        session_id: str,
        *,
        user_context: Mapping[str, str] | None = None,
        target_viewer_id: str | None = None,
        target_persona_id: str | None = None,
        trigger_event_ids: tuple[str, ...] = (),
        trigger_frame_ids: tuple[str, ...] = (),
    ) -> Observation:
        room_events = await self._room_service.read_events(
            session_id,
            max_count=self._max_events_per_observation,
        )

        async with self._lock:
            self._require_active_session(session_id)
            now = self._clock.now_ms()
            self._evict_expired(now)
            frames = self._latest_frames()
            observation = Observation(
                session_id=session_id,
                observation_id=self._id_generator.new_id(),
                created_at_ms=now,
                frames=frames,
                room_events=room_events,
                trigger_event_ids=trigger_event_ids,
                trigger_frame_ids=trigger_frame_ids,
                user_context={} if user_context is None else user_context,
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
            )

        await self._room_service.require_active_session(session_id)
        return observation

    def _reset(self, session_id: str) -> None:
        self._frames.clear()
        self._active_session_id = session_id

    def _require_active_session(self, session_id: str) -> None:
        if self._active_session_id != session_id:
            raise RoomSessionNotActiveError(session_id, self._active_session_id)

    def _evict_expired(self, now_ms: int) -> None:
        retained = (
            frame for frame in self._frames if now_ms - frame.created_at_ms < self._frame_ttl_ms
        )
        self._frames = deque(retained, maxlen=self._frame_capacity)

    def _latest_frames(self) -> tuple[FrameRef, ...]:
        if self._max_frames_per_observation == 0:
            return ()
        return tuple(self._frames)[-self._max_frames_per_observation :]
