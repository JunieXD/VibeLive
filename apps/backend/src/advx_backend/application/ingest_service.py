import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from advx_backend.application.context_builder import ContextBuilder
from advx_backend.application.ports.asr import AsrProvider, AudioChunk, TranscriptSegment
from advx_backend.application.ports.generation import SessionTaskScope
from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    FrameStore,
    IngestInputKind,
    IngestReceipt,
    IngestReceiptStage,
    TextInput,
)
from advx_backend.application.ports.session import Clock
from advx_backend.application.room_service import RoomService
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEventSource

logger = logging.getLogger(__name__)


class ObservationScheduler(Protocol):
    async def submit(self, observation: Observation) -> asyncio.Future[object | None]: ...

    async def cancel_session(self, session_id: str) -> None: ...


class IngestServiceError(RuntimeError):
    pass


class IngestSessionNotActiveError(IngestServiceError):
    def __init__(self, session_id: str, active_session_id: str | None) -> None:
        self.session_id = session_id
        self.active_session_id = active_session_id
        super().__init__(f"ingest session {session_id} is not active")


class DuplicateIngestInputError(IngestServiceError):
    def __init__(self, input_id: str) -> None:
        self.input_id = input_id
        super().__init__(f"ingest input {input_id} was already accepted")


class IngestInputOutOfOrderError(IngestServiceError):
    pass


class UnknownAudioInputError(IngestServiceError):
    def __init__(self, input_id: str) -> None:
        self.input_id = input_id
        super().__init__(f"audio input {input_id} is not pending")


class UnsupportedIngestFormatError(IngestServiceError):
    pass


class IngestCapacityExceededError(IngestServiceError):
    pass


@dataclass(slots=True)
class _TrackedInput:
    kind: IngestInputKind
    timestamp_ms: int
    accepted: bool = False


class IngestService:
    """Accept bounded media inputs and turn public inputs into observations."""

    def __init__(
        self,
        *,
        room_service: RoomService,
        context_builder: ContextBuilder,
        frame_store: FrameStore,
        asr_provider: AsrProvider,
        scheduler: ObservationScheduler,
        session_tasks: SessionTaskScope,
        clock: Clock,
        max_tracked_input_ids: int = 1_024,
    ) -> None:
        if max_tracked_input_ids < 1:
            raise ValueError("max_tracked_input_ids must be at least one")
        self._room_service = room_service
        self._context_builder = context_builder
        self._frame_store = frame_store
        self._asr_provider = asr_provider
        self._scheduler = scheduler
        self._session_tasks = session_tasks
        self._clock = clock
        self._max_tracked_input_ids = max_tracked_input_ids
        self._active_session_id: str | None = None
        self._seen_inputs: OrderedDict[str, _TrackedInput] = OrderedDict()
        self._timestamp_floors: dict[IngestInputKind, int] = {}
        self._pending_audio_id: str | None = None
        self._result_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start_session(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        async with self._lock:
            if self._active_session_id is not None:
                if self._active_session_id == session_id:
                    return
                raise IngestSessionNotActiveError(session_id, self._active_session_id)
            self._active_session_id = session_id
            self._reset_tracking()

        try:
            await self._frame_store.start_session(session_id)
            await self._asr_provider.start()
        except BaseException:
            try:
                await self._asr_provider.stop()
            finally:
                await self._frame_store.clear_session(session_id)
            async with self._lock:
                if self._active_session_id == session_id:
                    self._active_session_id = None
                    self._reset_tracking()
            raise

        self._result_task = asyncio.create_task(
            self._consume_asr_results(session_id),
            name=f"ingest-asr-results:{session_id}",
        )

    async def stop_session(self, session_id: str) -> None:
        async with self._lock:
            if self._active_session_id != session_id:
                return
            self._active_session_id = None
            self._reset_tracking()
            result_task = self._result_task
            self._result_task = None

        if result_task is not None:
            result_task.cancel()
            await asyncio.gather(result_task, return_exceptions=True)
        try:
            await self._scheduler.cancel_session(session_id)
        finally:
            try:
                await self._asr_provider.stop()
            finally:
                await self._frame_store.clear_session(session_id)

    async def submit_text(self, input: TextInput) -> IngestReceipt:
        if not input.text.strip():
            raise UnsupportedIngestFormatError("text input must not be blank")
        await self._require_running(input.session_id)
        await self._reserve(
            session_id=input.session_id,
            input_id=input.input_id,
            kind=IngestInputKind.TEXT,
            timestamp_ms=input.created_at_ms,
        )
        appended = False
        try:
            await self._room_service.append_event(
                input.session_id,
                source_type=RoomEventSource.USER_TEXT,
                source_id="host",
                text=input.text.strip(),
                payload={"input_id": input.input_id},
            )
            appended = True
            await self._schedule_observation(input.session_id)
        except BaseException:
            await self._settle(input.input_id, accepted=appended)
            raise
        await self._settle(input.input_id, accepted=True)
        return self._receipt(input.session_id, input.input_id, IngestInputKind.TEXT)

    async def submit_frame(self, input: FrameInput) -> IngestReceipt:
        if input.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise UnsupportedIngestFormatError(f"unsupported frame format: {input.mime_type}")
        await self._require_running(input.session_id)
        await self._reserve(
            session_id=input.session_id,
            input_id=input.input_id,
            kind=IngestInputKind.FRAME,
            timestamp_ms=input.captured_at_ms,
        )
        stored = False
        try:
            frame = await self._frame_store.store(input)
            stored = True
            await self._context_builder.append_frame_ref(input.session_id, frame)
            await self._schedule_observation(input.session_id)
        except BaseException:
            await self._settle(input.input_id, accepted=stored)
            raise
        await self._settle(input.input_id, accepted=True)
        return self._receipt(input.session_id, input.input_id, IngestInputKind.FRAME)

    async def submit_audio(self, input: AudioInput) -> IngestReceipt:
        sample_rate, channels, sample_width_bits = self._parse_audio_format(input.format)
        if len(input.body) % (channels * sample_width_bits // 8) != 0:
            raise UnsupportedIngestFormatError("audio body is not aligned to complete PCM samples")
        await self._require_running(input.session_id)
        await self._reserve_audio(input)
        pushed = False
        try:
            frame_count = len(input.body) // (channels * sample_width_bits // 8)
            duration_ms = (frame_count * 1_000) // sample_rate
            await self._asr_provider.push_audio(
                AudioChunk(
                    session_id=input.session_id,
                    started_at_ms=input.captured_at_ms,
                    ended_at_ms=input.captured_at_ms + duration_ms,
                    sample_rate=sample_rate,
                    channels=channels,
                    sample_width_bits=sample_width_bits,
                    pcm=input.body,
                )
            )
            pushed = True
        finally:
            await self._settle(input.input_id, accepted=pushed)
            if not pushed:
                await self._release_audio(input.input_id)
        return self._receipt(input.session_id, input.input_id, IngestInputKind.AUDIO)

    async def commit_audio(self, commit: AudioCommit) -> IngestReceipt:
        await self._require_running(commit.session_id)
        async with self._lock:
            self._require_active_locked(commit.session_id)
            if self._pending_audio_id != commit.input_id:
                raise UnknownAudioInputError(commit.input_id)
            if (
                last_audio_at_ms := self._timestamp_for(IngestInputKind.AUDIO)
            ) is not None and commit.committed_at_ms < last_audio_at_ms:
                raise IngestInputOutOfOrderError("audio commit precedes its captured input")
        await self._asr_provider.commit()
        await self._release_audio(commit.input_id)
        return self._receipt(
            commit.session_id,
            commit.input_id,
            IngestInputKind.AUDIO,
            stage=IngestReceiptStage.COMMITTED,
        )

    async def _consume_asr_results(self, session_id: str) -> None:
        while True:
            try:
                async for segment in self._asr_provider.results():
                    await self._handle_transcript(session_id, segment)
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not await self._is_active(session_id):
                    return
                logger.warning(
                    "ASR result stream failed",
                    extra={"session_id": session_id, "error_type": type(error).__name__},
                )

    async def _handle_transcript(self, session_id: str, segment: TranscriptSegment) -> None:
        if not segment.final or not segment.text.strip() or segment.session_id != session_id:
            return
        if not await self._session_tasks.accepts_results(session_id):
            return
        await self._room_service.append_event(
            session_id,
            source_type=RoomEventSource.USER_VOICE,
            source_id="host",
            text=segment.text.strip(),
            payload={
                "final": True,
                "started_at_ms": segment.started_at_ms,
                "ended_at_ms": segment.ended_at_ms,
            },
        )
        await self._schedule_observation(session_id)

    async def _schedule_observation(self, session_id: str) -> None:
        if not await self._session_tasks.accepts_results(session_id):
            return
        observation = await self._context_builder.build(session_id)
        await self._scheduler.submit(observation)

    async def _reserve(
        self,
        *,
        session_id: str,
        input_id: str,
        kind: IngestInputKind,
        timestamp_ms: int,
    ) -> None:
        async with self._lock:
            self._require_active_locked(session_id)
            self._require_unique_locked(input_id)
            last_timestamp = self._timestamp_for(kind)
            if last_timestamp is not None and timestamp_ms < last_timestamp:
                raise IngestInputOutOfOrderError(f"{kind.value} input is out of order")
            self._remember_locked(input_id, kind, timestamp_ms)

    async def _reserve_audio(self, input: AudioInput) -> None:
        async with self._lock:
            self._require_active_locked(input.session_id)
            self._require_unique_locked(input.input_id)
            if self._pending_audio_id is not None:
                raise IngestInputOutOfOrderError("the previous audio input is not committed")
            last_audio_at_ms = self._timestamp_for(IngestInputKind.AUDIO)
            if last_audio_at_ms is not None and input.captured_at_ms < last_audio_at_ms:
                raise IngestInputOutOfOrderError("audio input is out of order")
            self._remember_locked(
                input.input_id,
                IngestInputKind.AUDIO,
                input.captured_at_ms,
            )
            self._pending_audio_id = input.input_id

    async def _release_audio(self, input_id: str) -> None:
        async with self._lock:
            if self._pending_audio_id == input_id:
                self._pending_audio_id = None

    async def _settle(self, input_id: str, *, accepted: bool) -> None:
        async with self._lock:
            tracked = self._seen_inputs.get(input_id)
            if tracked is None:
                return
            if accepted:
                tracked.accepted = True
            else:
                self._seen_inputs.pop(input_id, None)

    async def _require_running(self, session_id: str) -> None:
        async with self._lock:
            self._require_active_locked(session_id)
        if not await self._session_tasks.accepts_results(session_id):
            raise IngestSessionNotActiveError(session_id, self._active_session_id)

    async def _is_active(self, session_id: str) -> bool:
        async with self._lock:
            return self._active_session_id == session_id

    def _require_active_locked(self, session_id: str) -> None:
        if self._active_session_id != session_id:
            raise IngestSessionNotActiveError(session_id, self._active_session_id)

    def _require_unique_locked(self, input_id: str) -> None:
        if input_id in self._seen_inputs:
            raise DuplicateIngestInputError(input_id)

    def _remember_locked(
        self,
        input_id: str,
        kind: IngestInputKind,
        timestamp_ms: int,
    ) -> None:
        self._seen_inputs[input_id] = _TrackedInput(kind=kind, timestamp_ms=timestamp_ms)
        while len(self._seen_inputs) > self._max_tracked_input_ids:
            evicted = next(
                (
                    (tracked_id, tracked)
                    for tracked_id, tracked in self._seen_inputs.items()
                    if tracked.accepted and tracked_id != self._pending_audio_id
                ),
                None,
            )
            if evicted is None:
                self._seen_inputs.pop(input_id, None)
                raise IngestCapacityExceededError("too many ingest inputs are in progress")
            evicted_id, tracked = evicted
            self._seen_inputs.pop(evicted_id)
            current_floor = self._timestamp_floors.get(tracked.kind)
            if current_floor is None or tracked.timestamp_ms > current_floor:
                self._timestamp_floors[tracked.kind] = tracked.timestamp_ms

    def _timestamp_for(self, kind: IngestInputKind) -> int | None:
        timestamps = [
            tracked.timestamp_ms for tracked in self._seen_inputs.values() if tracked.kind is kind
        ]
        floor = self._timestamp_floors.get(kind)
        if floor is not None:
            timestamps.append(floor)
        return max(timestamps, default=None)

    def _receipt(
        self,
        session_id: str,
        input_id: str,
        kind: IngestInputKind,
        *,
        stage: IngestReceiptStage = IngestReceiptStage.RECEIVED,
    ) -> IngestReceipt:
        return IngestReceipt(
            session_id=session_id,
            input_id=input_id,
            input_kind=kind,
            stage=stage,
            accepted_at_ms=self._clock.now_ms(),
        )

    @staticmethod
    def _parse_audio_format(value: str) -> tuple[int, int, int]:
        parts = [part.strip() for part in value.split(";")]
        if not parts or parts[0].casefold() != "audio/pcm":
            raise UnsupportedIngestFormatError("audio format must be audio/pcm")
        parameters: dict[str, str] = {}
        for part in parts[1:]:
            key, separator, parameter_value = part.partition("=")
            if not separator or not key or not parameter_value:
                raise UnsupportedIngestFormatError("audio format parameters are invalid")
            parameters[key.casefold()] = parameter_value.casefold()
        if parameters != {"rate": "16000", "channels": "1", "format": "s16le"}:
            raise UnsupportedIngestFormatError("audio must be mono 16 kHz PCM S16LE")
        return 16_000, 1, 16

    def _reset_tracking(self) -> None:
        self._seen_inputs.clear()
        self._timestamp_floors.clear()
        self._pending_audio_id = None
