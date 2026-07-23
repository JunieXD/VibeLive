import asyncio
from collections import OrderedDict

from advx_backend.application.ports.ingest import (
    FrameInput,
    FrameStoreLimits,
    ResolvedFrame,
)
from advx_backend.application.ports.session import IdGenerator
from advx_backend.domain.observation import FrameRef


class FrameStoreError(RuntimeError):
    pass


class FrameStoreSessionNotActiveError(FrameStoreError):
    def __init__(self, session_id: str, active_session_id: str | None) -> None:
        self.session_id = session_id
        self.active_session_id = active_session_id
        super().__init__(f"frame store session {session_id} is not active")


class FrameTooLargeError(FrameStoreError):
    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(f"frame body is {size} bytes; limit is {limit}")


class DuplicateFrameInputError(FrameStoreError):
    def __init__(self, input_id: str) -> None:
        self.input_id = input_id
        super().__init__(f"frame input {input_id} already exists")


class _StoredFrame:
    __slots__ = ("data_ref", "resolved")

    def __init__(self, *, data_ref: str, resolved: ResolvedFrame) -> None:
        self.data_ref = data_ref
        self.resolved = resolved


class InMemoryFrameStore:
    """Bounded, session-scoped storage for ephemeral frame bytes."""

    def __init__(self, *, limits: FrameStoreLimits, id_generator: IdGenerator) -> None:
        self._limits = limits
        self._id_generator = id_generator
        self._active_session_id: str | None = None
        self._frames: OrderedDict[str, _StoredFrame] = OrderedDict()
        self._input_ids: set[str] = set()
        self._total_bytes = 0
        self._lock = asyncio.Lock()

    @property
    def limits(self) -> FrameStoreLimits:
        return self._limits

    async def start_session(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        async with self._lock:
            if self._active_session_id is not None and self._active_session_id != session_id:
                raise FrameStoreSessionNotActiveError(session_id, self._active_session_id)
            self._clear()
            self._active_session_id = session_id

    async def stop_session(self, session_id: str) -> None:
        await self.clear_session(session_id)

    async def clear_session(self, session_id: str) -> None:
        async with self._lock:
            if self._active_session_id != session_id:
                return
            self._clear()
            self._active_session_id = None

    async def store(self, frame: FrameInput) -> FrameRef:
        body_size = len(frame.body)
        frame_limit = min(self._limits.max_frame_bytes, self._limits.max_total_bytes)
        if body_size > frame_limit:
            raise FrameTooLargeError(body_size, frame_limit)

        async with self._lock:
            self._require_active(frame.session_id)
            if frame.input_id in self._input_ids:
                raise DuplicateFrameInputError(frame.input_id)

            frame_id = self._id_generator.new_id()
            data_ref = f"advx-frame:{frame_id}"
            resolved = ResolvedFrame(
                session_id=frame.session_id,
                frame_id=frame_id,
                input_id=frame.input_id,
                captured_at_ms=frame.captured_at_ms,
                mime_type=frame.mime_type,
                body=frame.body,
            )
            self._frames[data_ref] = _StoredFrame(data_ref=data_ref, resolved=resolved)
            self._input_ids.add(frame.input_id)
            self._total_bytes += body_size
            self._evict_to_limits()
            return FrameRef(
                frame_id=frame_id,
                created_at_ms=frame.captured_at_ms,
                mime_type=frame.mime_type,
                data_ref=data_ref,
            )

    async def resolve(
        self,
        *,
        session_id: str,
        frame: FrameRef,
    ) -> ResolvedFrame | None:
        async with self._lock:
            self._require_active(session_id)
            stored = self._frames.get(frame.data_ref)
            if stored is None:
                return None
            resolved = stored.resolved
            if (
                resolved.session_id != session_id
                or resolved.frame_id != frame.frame_id
                or resolved.mime_type != frame.mime_type
                or resolved.captured_at_ms != frame.created_at_ms
            ):
                return None
            self._frames.move_to_end(frame.data_ref)
            return resolved

    def _evict_to_limits(self) -> None:
        while (
            len(self._frames) > self._limits.max_frames
            or self._total_bytes > self._limits.max_total_bytes
        ):
            _, stored = self._frames.popitem(last=False)
            self._input_ids.discard(stored.resolved.input_id)
            self._total_bytes -= len(stored.resolved.body)

    def _require_active(self, session_id: str) -> None:
        if self._active_session_id != session_id:
            raise FrameStoreSessionNotActiveError(session_id, self._active_session_id)

    def _clear(self) -> None:
        self._frames.clear()
        self._input_ids.clear()
        self._total_bytes = 0
