import asyncio
import hashlib
import logging
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from advx_backend.application.context_builder import ContextBuilder
from advx_backend.application.ports.asr import (
    AsrProvider,
    AudioChunk,
    AudioSource,
    TranscriptSegment,
    TranscriptTargetResolver,
)
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
from advx_backend.contracts.realtime import (
    MAX_TEXT_INPUT_LENGTH,
    AsrTranscriptEvent,
)
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEventSource

logger = logging.getLogger(__name__)

_COORDINATED_TURN_TIMEOUT_MS = 70_000
_SCREEN_CHANGE_TRIGGER_THRESHOLD = 0.2
_SCREEN_TRIGGER_COOLDOWN_MS = 10_000
_WINDOW_BATCH_MODE_POLL_MS = 250


class ObservationScheduler(Protocol):
    async def submit(self, observation: Observation) -> asyncio.Future[object | None]: ...

    async def cancel_session(self, session_id: str) -> None: ...


class TranscriptPublisher(Protocol):
    async def publish_transcript(self, event: AsrTranscriptEvent) -> None: ...


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
    format: str
    body_sha256: str
    source: AudioSource | None = None
    ended_at_ms: int | None = None
    connection_id: str | None = None
    accepted: bool = False
    fingerprints: dict[IngestReceiptStage, str] = field(default_factory=dict)
    receipts: dict[IngestReceiptStage, IngestReceipt] = field(default_factory=dict)


@dataclass(slots=True)
class _VoiceTurn:
    event_ids: list[str]
    target_viewer_id: str | None
    target_persona_id: str | None
    last_ended_at_ms: int
    task: asyncio.Task[None] | None = None


@dataclass(frozen=True, slots=True)
class _CommittedAudio:
    input_id: str
    source: AudioSource
    started_at_ms: int
    ended_at_ms: int
    turn_id: str | None = None


@dataclass(slots=True)
class _CoordinatedVoiceTurn:
    committed_sources: set[AudioSource] = field(default_factory=set)
    completed_sources: set[AudioSource] = field(default_factory=set)
    event_ids: list[str] = field(default_factory=list)
    system_audio_required: bool | None = None
    microphone_ended_at_ms: int | None = None
    target_viewer_id: str | None = None
    target_persona_id: str | None = None
    timeout_task: asyncio.Task[None] | None = None
    schedule_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _WindowBatchDelta:
    event_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    frame_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    targets: set[tuple[str, str]] = field(default_factory=set)
    user_context: dict[str, str] = field(default_factory=dict)

    def has_trigger(self) -> bool:
        return bool(self.event_ids or self.frame_ids)


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
        voice_target_resolver: TranscriptTargetResolver | None = None,
        max_final_transcript_attempts: int = 3,
        final_transcript_retry_backoff_ms: int = 25,
        voice_turn_silence_ms: int = 1_500,
        coordinated_turn_timeout_ms: int = 3_000,
        ambient_enabled: Callable[[str], Awaitable[bool]] | None = None,
        screen_trigger_settings: Callable[[str], Awaitable[tuple[float, int]]] | None = None,
        ambient_interval_ms: int = 30_000,
        ambient_interval_provider: Callable[[str], Awaitable[int]] | None = None,
        window_batch_schedule: (
            Callable[[str], Awaitable[tuple[bool, int]]] | None
        ) = None,
        window_batch_mode_poll_ms: int = _WINDOW_BATCH_MODE_POLL_MS,
        transcript_publisher: TranscriptPublisher | None = None,
    ) -> None:
        if max_tracked_input_ids < 1:
            raise ValueError("max_tracked_input_ids must be at least one")
        if max_final_transcript_attempts < 1:
            raise ValueError("max_final_transcript_attempts must be at least one")
        if final_transcript_retry_backoff_ms < 0:
            raise ValueError("final_transcript_retry_backoff_ms must not be negative")
        if voice_turn_silence_ms < 1:
            raise ValueError("voice_turn_silence_ms must be positive")
        if coordinated_turn_timeout_ms < voice_turn_silence_ms:
            raise ValueError(
                "coordinated_turn_timeout_ms must not be shorter than voice_turn_silence_ms"
            )
        if ambient_interval_ms < 1:
            raise ValueError("ambient_interval_ms must be positive")
        if window_batch_mode_poll_ms < 1:
            raise ValueError("window_batch_mode_poll_ms must be positive")
        self._room_service = room_service
        self._context_builder = context_builder
        self._frame_store = frame_store
        self._asr_provider = asr_provider
        self._scheduler = scheduler
        self._session_tasks = session_tasks
        self._clock = clock
        self._max_tracked_input_ids = max_tracked_input_ids
        self._voice_target_resolver = voice_target_resolver
        self._max_final_transcript_attempts = max_final_transcript_attempts
        self._final_transcript_retry_backoff_ms = final_transcript_retry_backoff_ms
        self._voice_turn_silence_ms = voice_turn_silence_ms
        self._coordinated_turn_timeout_ms = coordinated_turn_timeout_ms
        self._ambient_enabled = ambient_enabled
        self._screen_trigger_settings = screen_trigger_settings
        self._ambient_interval_ms = ambient_interval_ms
        self._ambient_interval_provider = ambient_interval_provider
        self._window_batch_schedule = window_batch_schedule
        self._window_batch_mode_poll_ms = window_batch_mode_poll_ms
        self._transcript_publisher = transcript_publisher
        self._active_session_id: str | None = None
        self._seen_inputs: OrderedDict[str, _TrackedInput] = OrderedDict()
        self._seen_utterances: OrderedDict[str, int] = OrderedDict()
        self._partial_transcripts: dict[tuple[str, AudioSource], TranscriptSegment] = {}
        self._voice_turns: dict[tuple[str, AudioSource], _VoiceTurn] = {}
        self._pending_final_audio: dict[AudioSource, deque[_CommittedAudio]] = {
            source: deque() for source in AudioSource
        }
        self._coordinated_turns: dict[str, _CoordinatedVoiceTurn] = {}
        self._coordinated_utterance_keys: OrderedDict[str, None] = OrderedDict()
        self._timestamp_floors: dict[tuple[IngestInputKind, AudioSource | None], int] = {}
        self._pending_audio_ids: dict[AudioSource, str] = {}
        self._last_trigger_at_ms: int | None = None
        self._result_task: asyncio.Task[None] | None = None
        self._ambient_task: asyncio.Task[None] | None = None
        self._window_batch_task: asyncio.Task[None] | None = None
        self._window_batch_delta = _WindowBatchDelta()
        self._window_batch_processed_event_ids: OrderedDict[str, None] = OrderedDict()
        self._window_batch_processed_frame_ids: OrderedDict[str, None] = OrderedDict()
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
        if self._window_batch_schedule is not None:
            self._window_batch_task = asyncio.create_task(
                self._run_window_batch_timer(session_id),
                name=f"ingest-window-batch:{session_id}",
            )

    def set_voice_target_resolver(
        self,
        resolver: TranscriptTargetResolver | None,
    ) -> None:
        self._voice_target_resolver = resolver

    async def notify_voice_activity(
        self,
        session_id: str,
        occurred_at_ms: int,
        source: AudioSource = AudioSource.MICROPHONE,
    ) -> None:
        """Keep a short pause inside one spoken turn when speech resumes."""
        if occurred_at_ms < 0:
            raise ValueError("occurred_at_ms must be non-negative")
        async with self._lock:
            if self._active_session_id != session_id:
                raise IngestSessionNotActiveError(session_id, self._active_session_id)
            turn = self._voice_turns.get((session_id, source))
            task = None if turn is None else turn.task
            if turn is not None:
                turn.task = None
        if task is not None:
            task.cancel()

    async def stop_session(self, session_id: str) -> None:
        async with self._lock:
            if self._active_session_id != session_id:
                return
            voice_tasks = tuple(
                turn.task
                for turn in self._voice_turns.values()
                if turn.task is not None
            )
            coordinated_tasks = tuple(
                task
                for turn in self._coordinated_turns.values()
                for task in (turn.timeout_task, turn.schedule_task)
                if task is not None
            )
            ambient_task = self._ambient_task
            self._ambient_task = None
            window_batch_task = self._window_batch_task
            self._window_batch_task = None
            self._active_session_id = None
            self._reset_tracking()
            result_task = self._result_task
            self._result_task = None

        if result_task is not None:
            result_task.cancel()
            await asyncio.gather(result_task, return_exceptions=True)
        for task in voice_tasks:
            task.cancel()
        if voice_tasks:
            await asyncio.gather(*voice_tasks, return_exceptions=True)
        for task in coordinated_tasks:
            task.cancel()
        if coordinated_tasks:
            await asyncio.gather(*coordinated_tasks, return_exceptions=True)
        if ambient_task is not None:
            ambient_task.cancel()
            await asyncio.gather(ambient_task, return_exceptions=True)
        if window_batch_task is not None:
            window_batch_task.cancel()
            await asyncio.gather(window_batch_task, return_exceptions=True)
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
        fingerprint = self._input_fingerprint(
            kind=IngestInputKind.TEXT,
            source=None,
            timestamp_ms=input.created_at_ms,
            format_value=self._text_format(input),
            body=input.text.encode("utf-8"),
            stage=IngestReceiptStage.RECEIVED,
        )
        cached = await self._reserve(
            session_id=input.session_id,
            input_id=input.input_id,
            kind=IngestInputKind.TEXT,
            timestamp_ms=input.created_at_ms,
            format_value=self._text_format(input),
            body_sha256=hashlib.sha256(input.text.encode("utf-8")).hexdigest(),
            fingerprint=fingerprint,
        )
        if cached is not None:
            return cached
        receipt = self._receipt(input.session_id, input.input_id, IngestInputKind.TEXT)
        appended = False
        try:
            payload = {"input_id": input.input_id}
            if input.target_viewer_id is not None:
                payload["target_viewer_id"] = input.target_viewer_id
            if input.target_persona_id is not None:
                payload["target_persona_id"] = input.target_persona_id
            event = await self._room_service.append_event(
                input.session_id,
                source_type=RoomEventSource.USER_TEXT,
                source_id="host",
                text=input.text.strip(),
                payload=payload,
            )
            appended = True
            await self._schedule_observation(
                input.session_id,
                trigger_event_ids=(event.event_id,),
                target_viewer_id=input.target_viewer_id,
                target_persona_id=input.target_persona_id,
            )
            await self._restart_ambient_timer(input.session_id)
        except BaseException:
            await self._settle(
                input.input_id,
                accepted=appended,
                receipt=receipt if appended else None,
            )
            raise
        await self._settle(input.input_id, accepted=True, receipt=receipt)
        return receipt

    async def submit_frame(self, input: FrameInput) -> IngestReceipt:
        if input.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise UnsupportedIngestFormatError(f"unsupported frame format: {input.mime_type}")
        await self._require_running(input.session_id)
        fingerprint = self._input_fingerprint(
            kind=IngestInputKind.FRAME,
            source=None,
            timestamp_ms=input.captured_at_ms,
            format_value=input.mime_type,
            body=input.body,
            stage=IngestReceiptStage.RECEIVED,
        )
        cached = await self._reserve(
            session_id=input.session_id,
            input_id=input.input_id,
            kind=IngestInputKind.FRAME,
            timestamp_ms=input.captured_at_ms,
            format_value=input.mime_type,
            body_sha256=hashlib.sha256(input.body).hexdigest(),
            fingerprint=fingerprint,
        )
        if cached is not None:
            return cached
        receipt = self._receipt(input.session_id, input.input_id, IngestInputKind.FRAME)
        stored = False
        try:
            frame = await self._frame_store.store(input)
            stored = True
            await self._context_builder.append_frame_ref(input.session_id, frame)
            await self._schedule_screen_change_if_cold(
                input.session_id,
                frame_id=frame.frame_id,
                change_score=input.change_score,
            )
        except BaseException:
            await self._settle(
                input.input_id,
                accepted=stored,
                receipt=receipt if stored else None,
            )
            raise
        await self._settle(input.input_id, accepted=True, receipt=receipt)
        return receipt

    async def submit_audio(self, input: AudioInput) -> IngestReceipt:
        sample_rate, channels, sample_width_bits = self._parse_audio_format(input.format)
        if len(input.body) % (channels * sample_width_bits // 8) != 0:
            raise UnsupportedIngestFormatError("audio body is not aligned to complete PCM samples")
        frame_count = len(input.body) // (channels * sample_width_bits // 8)
        duration_ms = (frame_count * 1_000) // sample_rate
        await self._require_running(input.session_id)
        fingerprint = self._audio_fingerprint(input, IngestReceiptStage.RECEIVED)
        cached = await self._reserve_audio(
            input,
            ended_at_ms=input.captured_at_ms + duration_ms,
            fingerprint=fingerprint,
        )
        if cached is not None:
            return cached
        pushed = False
        try:
            await self._asr_provider.push_audio(
                AudioChunk(
                    session_id=input.session_id,
                    source=input.source,
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
                await self._release_audio(input.input_id, input.source)
        receipt = self._receipt(input.session_id, input.input_id, IngestInputKind.AUDIO)
        await self._settle(input.input_id, accepted=True, receipt=receipt)
        return receipt

    async def submit_audio_and_commit(self, input: AudioInput) -> IngestReceipt:
        sample_rate, channels, sample_width_bits = self._parse_audio_format(input.format)
        if len(input.body) % (channels * sample_width_bits // 8) != 0:
            raise UnsupportedIngestFormatError("audio body is not aligned to complete PCM samples")
        frame_count = len(input.body) // (channels * sample_width_bits // 8)
        duration_ms = (frame_count * 1_000) // sample_rate
        await self._require_running(input.session_id)
        committed_fingerprint = self._audio_fingerprint(
            input,
            IngestReceiptStage.COMMITTED,
        )
        cached = await self._reserve_audio(
            input,
            ended_at_ms=input.captured_at_ms + duration_ms,
            fingerprint=committed_fingerprint,
            stage=IngestReceiptStage.COMMITTED,
        )
        if cached is not None:
            return cached
        committed_audio = _CommittedAudio(
            input_id=input.input_id,
            source=input.source,
            started_at_ms=input.captured_at_ms,
            ended_at_ms=input.captured_at_ms + duration_ms,
            turn_id=input.turn_id,
        )
        registered = False
        try:
            await self._asr_provider.push_audio(
                AudioChunk(
                    session_id=input.session_id,
                    source=input.source,
                    started_at_ms=input.captured_at_ms,
                    ended_at_ms=input.captured_at_ms + duration_ms,
                    sample_rate=sample_rate,
                    channels=channels,
                    sample_width_bits=sample_width_bits,
                    pcm=input.body,
                )
            )
            async with self._lock:
                self._register_committed_audio_locked(
                    input.session_id,
                    AudioCommit(
                        session_id=input.session_id,
                        input_id=input.input_id,
                        committed_at_ms=input.captured_at_ms + duration_ms,
                        source=input.source,
                        turn_id=input.turn_id,
                        system_audio_required=input.system_audio_required,
                        connection_id=input.connection_id,
                    ),
                    committed_audio,
                )
                registered = True
            await self._asr_provider.commit(input.source)
        except BaseException:
            if registered:
                await self._discard_committed_audio(input.session_id, committed_audio)
            await self._release_audio(input.input_id, input.source)
            await self._settle(input.input_id, accepted=False)
            await self._discard_provider_audio(input.source)
            raise
        await self._release_audio(input.input_id, input.source)
        receipt = self._receipt(
            input.session_id,
            input.input_id,
            IngestInputKind.AUDIO,
            stage=IngestReceiptStage.COMMITTED,
        )
        await self._settle(input.input_id, accepted=True, receipt=receipt)
        return receipt

    async def commit_audio(self, commit: AudioCommit) -> IngestReceipt:
        await self._require_running(commit.session_id)
        committed_audio: _CommittedAudio | None = None
        async with self._lock:
            self._require_active_locked(commit.session_id)
            tracked = self._seen_inputs.get(commit.input_id)
            if tracked is not None:
                fingerprint = self._fingerprint(
                    kind=IngestInputKind.AUDIO,
                    source=commit.source,
                    timestamp_ms=tracked.timestamp_ms,
                    format_value=(
                        f"{tracked.format};turn_id={commit.turn_id or ''};"
                        f"system_audio_required={str(commit.system_audio_required).lower()}"
                    ),
                    body_sha256=tracked.body_sha256,
                    stage=IngestReceiptStage.COMMITTED,
                )
                existing = tracked.fingerprints.get(IngestReceiptStage.COMMITTED)
                if existing is not None:
                    if existing != fingerprint:
                        raise DuplicateIngestInputError(commit.input_id)
                    receipt = tracked.receipts.get(IngestReceiptStage.COMMITTED)
                    if receipt is not None:
                        return receipt
            if self._pending_audio_ids.get(commit.source) != commit.input_id:
                raise UnknownAudioInputError(commit.input_id)
            if (
                last_audio_at_ms := self._timestamp_for(
                    IngestInputKind.AUDIO,
                    commit.source,
                )
            ) is not None and commit.committed_at_ms < last_audio_at_ms:
                raise IngestInputOutOfOrderError("audio commit precedes its captured input")
            if tracked is None or tracked.source is not commit.source:
                raise UnknownAudioInputError(commit.input_id)
            if (
                tracked.connection_id is not None
                and commit.connection_id != tracked.connection_id
            ):
                raise UnknownAudioInputError(commit.input_id)
            tracked.fingerprints[IngestReceiptStage.COMMITTED] = fingerprint
            committed_audio = _CommittedAudio(
                input_id=commit.input_id,
                source=commit.source,
                started_at_ms=tracked.timestamp_ms,
                ended_at_ms=tracked.ended_at_ms or tracked.timestamp_ms,
                turn_id=commit.turn_id,
            )
            self._register_committed_audio_locked(
                commit.session_id,
                commit,
                committed_audio,
            )
        try:
            await self._asr_provider.commit(commit.source)
        except BaseException:
            assert committed_audio is not None
            await self._discard_committed_audio(commit.session_id, committed_audio)
            await self._release_audio(commit.input_id, commit.source)
            await self._settle(commit.input_id, accepted=False)
            await self._discard_provider_audio(commit.source)
            raise
        await self._release_audio(commit.input_id, commit.source)
        receipt = self._receipt(
            commit.session_id,
            commit.input_id,
            IngestInputKind.AUDIO,
            stage=IngestReceiptStage.COMMITTED,
        )
        await self._settle(commit.input_id, accepted=True, receipt=receipt)
        return receipt

    async def clear_connection(self, connection_id: str) -> None:
        if not connection_id:
            return
        sources: list[AudioSource] = []
        async with self._lock:
            for source, input_id in tuple(self._pending_audio_ids.items()):
                tracked = self._seen_inputs.get(input_id)
                if tracked is None or tracked.connection_id != connection_id:
                    continue
                self._pending_audio_ids.pop(source, None)
                self._seen_inputs.pop(input_id, None)
                sources.append(source)
        for source in sources:
            await self._discard_provider_audio(source)

    async def _consume_asr_results(self, session_id: str) -> None:
        while True:
            try:
                async for segment in self._asr_provider.results():
                    await self._consume_transcript(session_id, segment)
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not await self._is_active(session_id):
                    return
                status_code = getattr(error, "status_code", None)
                utterance_id = getattr(error, "utterance_id", None)
                logger.warning(
                    "ASR result stream failed: %s",
                    error,
                    extra={
                        "session_id": session_id,
                        "utterance_id": (
                            utterance_id if isinstance(utterance_id, str) else None
                        ),
                        "upstream_http_status": (
                            status_code if isinstance(status_code, int) else None
                        ),
                        "retryable": bool(getattr(error, "retryable", False)),
                        "error_type": type(error).__name__,
                    },
                )

    async def _consume_transcript(
        self,
        session_id: str,
        segment: TranscriptSegment,
    ) -> None:
        attempts = self._max_final_transcript_attempts if segment.final else 1
        for attempt in range(1, attempts + 1):
            try:
                await self._handle_transcript(session_id, segment)
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if attempt >= attempts:
                    logger.error(
                        "final ASR segment exhausted bounded retries",
                        extra={
                            "session_id": session_id,
                            "utterance_id": segment.utterance_id,
                            "revision": segment.revision,
                            "attempts": attempts,
                            "error_type": type(error).__name__,
                        },
                    )
                    if segment.final:
                        await self._discard_failed_final(segment)
                    return
                if self._final_transcript_retry_backoff_ms:
                    await asyncio.sleep(
                        self._final_transcript_retry_backoff_ms / 1_000
                    )

    async def _discard_failed_final(self, segment: TranscriptSegment) -> None:
        tasks_to_cancel: list[asyncio.Task[None]] = []
        async with self._lock:
            committed_audio = self._committed_audio_for_segment_locked(segment)
            self._consume_committed_audio_locked(segment.source, committed_audio)
            if committed_audio is not None and committed_audio.turn_id is not None:
                turn = self._coordinated_turns.pop(committed_audio.turn_id, None)
                if turn is not None:
                    tasks_to_cancel.extend(
                        task
                        for task in (turn.timeout_task, turn.schedule_task)
                        if task is not None
                    )
        for task in tasks_to_cancel:
            if task is not asyncio.current_task() and not task.done():
                task.cancel()

    async def _handle_transcript(self, session_id: str, segment: TranscriptSegment) -> None:
        if segment.session_id != session_id:
            return
        text = segment.text.strip()
        if not segment.final:
            if not text:
                return
            if len(text) > MAX_TEXT_INPUT_LENGTH:
                logger.warning(
                    "discarded ASR transcript exceeding the Room text limit",
                    extra={
                        "session_id": session_id,
                        "audio_source": segment.source.value,
                        "text_length": len(text),
                    },
                )
                return
            async with self._lock:
                if self._active_session_id == session_id:
                    self._partial_transcripts[(session_id, segment.source)] = segment
            await self._publish_transcript(segment)
            return
        if not await self._session_tasks.accepts_results(session_id):
            return
        # Providers may resend a final segment; utterance IDs make this boundary idempotent.
        utterance_id = segment.utterance_id or (
            f"{session_id}:{segment.started_at_ms}:"
            f"{segment.ended_at_ms}:{segment.text.strip()}"
        )
        utterance_key = f"{session_id}\0{segment.source.value}\0{utterance_id}"
        async with self._lock:
            if (
                self._seen_utterances.get(utterance_key, 0) >= segment.revision
                or utterance_key in self._coordinated_utterance_keys
            ):
                return
            committed_audio = self._committed_audio_for_segment_locked(segment)

        if len(text) > MAX_TEXT_INPUT_LENGTH:
            logger.warning(
                "discarded ASR transcript exceeding the Room text limit",
                extra={
                    "session_id": session_id,
                    "audio_source": segment.source.value,
                    "text_length": len(text),
                },
            )
            await self._finish_final_transcript(
                session_id,
                segment=segment,
                utterance_key=utterance_key,
                committed_audio=committed_audio,
                event_id=None,
                text_present=False,
                valid=False,
            )
            return

        if not text:
            await self._finish_final_transcript(
                session_id,
                segment=segment,
                utterance_key=utterance_key,
                committed_audio=committed_audio,
                event_id=None,
                text_present=False,
                valid=True,
            )
            return

        target_viewer_id: str | None = None
        target_persona_id: str | None = None
        target_payload: dict[str, object] = {}
        if (
            segment.source is AudioSource.MICROPHONE
            and self._voice_target_resolver is not None
        ):
            resolution = await self._voice_target_resolver.resolve(segment)
            target_payload = {
                "target_resolver_id": resolution.resolver_id,
                "target_ambiguous": resolution.ambiguous,
            }
            if not resolution.ambiguous:
                target_viewer_id = resolution.target_viewer_id
                target_persona_id = resolution.target_persona_id
                if target_viewer_id is not None:
                    target_payload["target_viewer_id"] = target_viewer_id
                if target_persona_id is not None:
                    target_payload["target_persona_id"] = target_persona_id
        payload: dict[str, object] = {
            "final": True,
            "started_at_ms": segment.started_at_ms,
            "ended_at_ms": segment.ended_at_ms,
            "utterance_id": utterance_id,
            "revision": segment.revision,
            "audio_source": segment.source.value,
            **target_payload,
        }
        if committed_audio is not None and committed_audio.turn_id is not None:
            payload["turn_id"] = committed_audio.turn_id
        if segment.source is AudioSource.SYSTEM_AUDIO:
            payload["event"] = "system_audio_transcript"
        event = await self._room_service.append_event(
            session_id,
            source_type=(
                RoomEventSource.USER_VOICE
                if segment.source is AudioSource.MICROPHONE
                else RoomEventSource.SYSTEM_EVENT
            ),
            source_id=(
                "host"
                if segment.source is AudioSource.MICROPHONE
                else "system-audio"
            ),
            text=text,
            payload=payload,
        )
        window_batch_enabled, _ = await self._window_batch_settings(session_id)
        if window_batch_enabled:
            await self._accumulate_window_batch_delta(
                session_id,
                trigger_event_ids=(event.event_id,),
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
            )
        await self._finish_final_transcript(
            session_id,
            segment=segment,
            utterance_key=utterance_key,
            committed_audio=committed_audio,
            event_id=event.event_id,
            text_present=True,
            valid=True,
            target_viewer_id=target_viewer_id,
            target_persona_id=target_persona_id,
        )
        await self._publish_transcript(
            segment.model_copy(update={"utterance_id": utterance_id})
        )
        if committed_audio is None or committed_audio.turn_id is None:
            await self._queue_voice_turn(
                session_id,
                source=segment.source,
                event_id=event.event_id,
                ended_at_ms=segment.ended_at_ms,
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
            )

    async def _finish_final_transcript(
        self,
        session_id: str,
        *,
        segment: TranscriptSegment,
        utterance_key: str,
        committed_audio: _CommittedAudio | None,
        event_id: str | None,
        text_present: bool,
        valid: bool,
        target_viewer_id: str | None = None,
        target_persona_id: str | None = None,
    ) -> None:
        tasks_to_cancel: list[asyncio.Task[None]] = []
        async with self._lock:
            self._consume_committed_audio_locked(segment.source, committed_audio)
            self._remember_utterance_locked(utterance_key, segment.revision)
            self._partial_transcripts.pop((session_id, segment.source), None)
            if committed_audio is not None and committed_audio.turn_id is not None:
                self._remember_coordinated_utterance_locked(utterance_key)
                tasks_to_cancel.extend(
                    self._complete_coordinated_turn_locked(
                        session_id,
                        turn_id=committed_audio.turn_id,
                        source=segment.source,
                        event_id=event_id,
                        text_present=text_present,
                        valid=valid,
                        target_viewer_id=target_viewer_id,
                        target_persona_id=target_persona_id,
                    )
                )
        current_task = asyncio.current_task()
        for task in tasks_to_cancel:
            if task is not current_task and not task.done():
                task.cancel()

    def _register_committed_audio_locked(
        self,
        session_id: str,
        commit: AudioCommit,
        committed_audio: _CommittedAudio,
    ) -> None:
        if commit.turn_id is not None:
            turn = self._coordinated_turns.get(commit.turn_id)
            if turn is None:
                turn = _CoordinatedVoiceTurn()
                self._coordinated_turns[commit.turn_id] = turn
            if commit.source in turn.committed_sources:
                raise IngestInputOutOfOrderError("audio source was already committed for this turn")
            turn.committed_sources.add(commit.source)
            if commit.source is AudioSource.MICROPHONE:
                turn.system_audio_required = commit.system_audio_required
                turn.microphone_ended_at_ms = committed_audio.ended_at_ms
        self._pending_final_audio[commit.source].append(committed_audio)

    async def _discard_committed_audio(
        self,
        session_id: str,
        committed_audio: _CommittedAudio,
    ) -> None:
        tasks_to_cancel: list[asyncio.Task[None]] = []
        async with self._lock:
            self._remove_committed_audio_locked(committed_audio.source, committed_audio)
            if committed_audio.turn_id is not None:
                turn = self._coordinated_turns.get(committed_audio.turn_id)
                if turn is not None:
                    self._coordinated_turns.pop(committed_audio.turn_id, None)
                    tasks_to_cancel.extend(
                        task
                        for task in (turn.timeout_task, turn.schedule_task)
                        if task is not None
                    )
        current_task = asyncio.current_task()
        for task in tasks_to_cancel:
            if task is not current_task and not task.done():
                task.cancel()

    def _consume_committed_audio_locked(
        self,
        source: AudioSource,
        committed_audio: _CommittedAudio | None,
    ) -> None:
        if committed_audio is None:
            return
        pending = self._pending_final_audio[source]
        for index, item in enumerate(pending):
            if item is committed_audio:
                for _ in range(index + 1):
                    pending.popleft()
                return

    def _remove_committed_audio_locked(
        self,
        source: AudioSource,
        committed_audio: _CommittedAudio,
    ) -> None:
        pending = self._pending_final_audio[source]
        for index, item in enumerate(pending):
            if item is committed_audio:
                del pending[index]
                return

    def _committed_audio_for_segment_locked(
        self,
        segment: TranscriptSegment,
    ) -> _CommittedAudio | None:
        pending = self._pending_final_audio[segment.source]
        for committed_audio in pending:
            if (
                committed_audio.started_at_ms == segment.started_at_ms
                and committed_audio.ended_at_ms == segment.ended_at_ms
            ):
                return committed_audio
        for committed_audio in pending:
            if (
                segment.started_at_ms < committed_audio.ended_at_ms
                and committed_audio.started_at_ms < segment.ended_at_ms
            ):
                return committed_audio
        return pending[0] if pending else None

    def _complete_coordinated_turn_locked(
        self,
        session_id: str,
        *,
        turn_id: str,
        source: AudioSource,
        event_id: str | None,
        text_present: bool,
        valid: bool,
        target_viewer_id: str | None,
        target_persona_id: str | None,
    ) -> list[asyncio.Task[None]]:
        turn = self._coordinated_turns.get(turn_id)
        if turn is None or source not in turn.committed_sources:
            return []
        if not valid or (source is AudioSource.MICROPHONE and not text_present):
            return self._discard_coordinated_turn_locked(turn_id, turn)

        turn.completed_sources.add(source)
        if event_id is not None:
            turn.event_ids.append(event_id)
        if source is AudioSource.MICROPHONE:
            turn.target_viewer_id = target_viewer_id
            turn.target_persona_id = target_persona_id
        if not self._coordinated_turn_ready(turn):
            if (
                source is AudioSource.MICROPHONE
                and turn.system_audio_required
                and turn.timeout_task is None
            ):
                turn.timeout_task = asyncio.create_task(
                    self._expire_coordinated_turn(session_id, turn_id, turn),
                    name=f"ingest-coordinated-turn-timeout:{session_id}:{turn_id}",
                )
            return []
        if turn.schedule_task is not None:
            return []

        turn.schedule_task = asyncio.create_task(
            self._finalize_coordinated_turn(session_id, turn_id, turn),
            name=f"ingest-coordinated-turn:{session_id}:{turn_id}",
        )
        return [] if turn.timeout_task is None else [turn.timeout_task]

    @staticmethod
    def _coordinated_turn_ready(turn: _CoordinatedVoiceTurn) -> bool:
        if (
            turn.system_audio_required is None
            or turn.microphone_ended_at_ms is None
            or AudioSource.MICROPHONE not in turn.completed_sources
        ):
            return False
        return (
            not turn.system_audio_required
            or AudioSource.SYSTEM_AUDIO in turn.completed_sources
        )

    def _discard_coordinated_turn_locked(
        self,
        turn_id: str,
        turn: _CoordinatedVoiceTurn,
    ) -> list[asyncio.Task[None]]:
        if self._coordinated_turns.get(turn_id) is turn:
            self._coordinated_turns.pop(turn_id, None)
        return [
            task
            for task in (turn.timeout_task, turn.schedule_task)
            if task is not None
        ]

    async def _expire_coordinated_turn(
        self,
        session_id: str,
        turn_id: str,
        turn: _CoordinatedVoiceTurn,
    ) -> None:
        try:
            await asyncio.sleep(self._coordinated_turn_timeout_ms / 1_000)
            async with self._lock:
                if (
                    self._active_session_id != session_id
                    or self._coordinated_turns.get(turn_id) is not turn
                    or turn.timeout_task is not asyncio.current_task()
                    or AudioSource.MICROPHONE not in turn.completed_sources
                ):
                    return
                self._coordinated_turns.pop(turn_id, None)
                event_ids = tuple(turn.event_ids)
                target_viewer_id = turn.target_viewer_id
                target_persona_id = turn.target_persona_id
            logger.warning(
                "coordinated ASR turn degraded after required system audio timed out",
                extra={"session_id": session_id, "turn_id": turn_id},
            )
            await self._schedule_observation(
                session_id,
                trigger_event_ids=event_ids,
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
                user_context={
                    "turn_id": turn_id,
                    "system_audio_degraded": "true",
                },
            )
            await self._restart_ambient_timer(session_id)
        except asyncio.CancelledError:
            raise

    async def _finalize_coordinated_turn(
        self,
        session_id: str,
        turn_id: str,
        turn: _CoordinatedVoiceTurn,
    ) -> None:
        try:
            assert turn.microphone_ended_at_ms is not None
            remaining_ms = max(
                0,
                turn.microphone_ended_at_ms
                + self._voice_turn_silence_ms
                - self._clock.now_ms(),
            )
            await asyncio.sleep(remaining_ms / 1_000)
            async with self._lock:
                if (
                    self._active_session_id != session_id
                    or self._coordinated_turns.get(turn_id) is not turn
                    or turn.schedule_task is not asyncio.current_task()
                ):
                    return
                self._coordinated_turns.pop(turn_id, None)
                event_ids = tuple(turn.event_ids)
                target_viewer_id = turn.target_viewer_id
                target_persona_id = turn.target_persona_id
            await self._schedule_observation(
                session_id,
                trigger_event_ids=event_ids,
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
            )
            await self._restart_ambient_timer(session_id)
        except asyncio.CancelledError:
            raise

    def _remember_utterance_locked(self, utterance_key: str, revision: int) -> None:
        committed_revision = self._seen_utterances.get(utterance_key, 0)
        if revision > committed_revision:
            self._seen_utterances[utterance_key] = revision
            self._seen_utterances.move_to_end(utterance_key)
            while len(self._seen_utterances) > self._max_tracked_input_ids:
                self._seen_utterances.popitem(last=False)

    def _remember_coordinated_utterance_locked(self, utterance_key: str) -> None:
        self._coordinated_utterance_keys[utterance_key] = None
        self._coordinated_utterance_keys.move_to_end(utterance_key)
        while len(self._coordinated_utterance_keys) > self._max_tracked_input_ids:
            self._coordinated_utterance_keys.popitem(last=False)

    async def _queue_voice_turn(
        self,
        session_id: str,
        *,
        source: AudioSource,
        event_id: str,
        ended_at_ms: int,
        target_viewer_id: str | None,
        target_persona_id: str | None,
    ) -> None:
        if source is AudioSource.SYSTEM_AUDIO:
            await self._schedule_observation(
                session_id,
                trigger_event_ids=(event_id,),
            )
            await self._restart_ambient_timer(session_id)
            return
        previous_task: asyncio.Task[None] | None = None
        async with self._lock:
            if self._active_session_id != session_id:
                return
            turn_key = (session_id, source)
            turn = self._voice_turns.get(turn_key)
            if turn is None:
                turn = _VoiceTurn(
                    event_ids=[],
                    target_viewer_id=target_viewer_id,
                    target_persona_id=target_persona_id,
                    last_ended_at_ms=ended_at_ms,
                )
                self._voice_turns[turn_key] = turn
            else:
                previous_task = turn.task
                turn.last_ended_at_ms = max(turn.last_ended_at_ms, ended_at_ms)
                if (
                    turn.target_viewer_id != target_viewer_id
                    or turn.target_persona_id != target_persona_id
                ):
                    turn.target_viewer_id = None
                    turn.target_persona_id = None
            turn.event_ids.append(event_id)
            turn.task = asyncio.create_task(
                self._finalize_voice_turn(session_id, source, turn),
                name=f"ingest-voice-turn:{session_id}:{source.value}",
            )
        if previous_task is not None:
            previous_task.cancel()

    async def _finalize_voice_turn(
        self,
        session_id: str,
        source: AudioSource,
        turn: _VoiceTurn,
    ) -> None:
        try:
            remaining_ms = max(
                0,
                turn.last_ended_at_ms + self._voice_turn_silence_ms - self._clock.now_ms(),
            )
            await asyncio.sleep(remaining_ms / 1_000)
            async with self._lock:
                if (
                    self._active_session_id != session_id
                    or self._voice_turns.get((session_id, source)) is not turn
                    or turn.task is not asyncio.current_task()
                ):
                    return
                self._voice_turns.pop((session_id, source), None)
                event_ids = tuple(turn.event_ids)
                target_viewer_id = turn.target_viewer_id
                target_persona_id = turn.target_persona_id
            await self._schedule_observation(
                session_id,
                trigger_event_ids=event_ids,
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
            )
            await self._restart_ambient_timer(session_id)
        except asyncio.CancelledError:
            raise

    async def _restart_ambient_timer(self, session_id: str) -> None:
        if self._ambient_enabled is None:
            return
        window_batch_enabled, _ = await self._window_batch_settings(session_id)
        if window_batch_enabled:
            async with self._lock:
                if self._active_session_id != session_id:
                    return
                previous = self._ambient_task
                self._ambient_task = None
            if previous is not None:
                previous.cancel()
            return
        async with self._lock:
            if self._active_session_id != session_id:
                return
            previous = self._ambient_task
            self._ambient_task = asyncio.create_task(
                self._run_ambient_timer(session_id),
                name=f"ingest-ambient:{session_id}",
            )
        if previous is not None:
            previous.cancel()

    async def _run_ambient_timer(self, session_id: str) -> None:
        try:
            while True:
                interval_ms = self._ambient_interval_ms
                if self._ambient_interval_provider is not None:
                    interval_ms = await self._ambient_interval_provider(session_id)
                    if interval_ms < 1:
                        raise ValueError("ambient interval provider returned a non-positive value")
                await asyncio.sleep(interval_ms / 1_000)
                if not await self._is_active(session_id):
                    return
                if self._ambient_enabled is None or not await self._ambient_enabled(session_id):
                    return
                await self._schedule_observation(
                    session_id,
                    user_context={"ambient": "true"},
                )
        except asyncio.CancelledError:
            raise

    async def _run_window_batch_timer(self, session_id: str) -> None:
        try:
            while True:
                if not await self._is_active(session_id):
                    return
                enabled, interval_ms = await self._window_batch_settings(session_id)
                if not enabled:
                    await self._discard_window_batch_delta(session_id)
                    await asyncio.sleep(self._window_batch_mode_poll_ms / 1_000)
                    continue
                await asyncio.sleep(interval_ms / 1_000)
                if not await self._is_active(session_id):
                    return
                still_enabled, current_interval_ms = await self._window_batch_settings(
                    session_id
                )
                if not still_enabled or current_interval_ms != interval_ms:
                    continue
                delta = await self._take_window_batch_delta(session_id)
                user_context = dict(delta.user_context)
                user_context["window_batch"] = "true"
                if delta.has_trigger():
                    user_context.pop("ambient", None)
                else:
                    user_context["ambient"] = "true"
                target_viewer_id, target_persona_id = self._window_batch_target(delta)
                try:
                    await self._schedule_observation(
                        session_id,
                        trigger_event_ids=tuple(delta.event_ids),
                        trigger_frame_ids=tuple(delta.frame_ids),
                        target_viewer_id=target_viewer_id,
                        target_persona_id=target_persona_id,
                        user_context=user_context,
                        window_batch_tick=True,
                    )
                except Exception:
                    await self._restore_window_batch_delta(session_id, delta)
                    logger.exception(
                        "window batch tick could not schedule an observation",
                        extra={"session_id": session_id},
                    )
        except asyncio.CancelledError:
            raise

    async def _window_batch_settings(self, session_id: str) -> tuple[bool, int]:
        if self._window_batch_schedule is None:
            return False, 5_000
        enabled, interval_ms = await self._window_batch_schedule(session_id)
        if not isinstance(enabled, bool):
            raise ValueError("window batch schedule returned a non-boolean enabled flag")
        if (
            isinstance(interval_ms, bool)
            or not isinstance(interval_ms, int)
            or interval_ms < 1
        ):
            raise ValueError("window batch schedule returned a non-positive interval")
        return enabled, interval_ms

    def partial_transcript_snapshot(
        self,
        session_id: str,
        source: AudioSource = AudioSource.MICROPHONE,
    ) -> TranscriptSegment | None:
        return self._partial_transcripts.get((session_id, source))

    async def _publish_transcript(self, segment: TranscriptSegment) -> None:
        if self._transcript_publisher is None:
            return
        try:
            await self._transcript_publisher.publish_transcript(
                AsrTranscriptEvent(
                    source=segment.source,
                    text=segment.text.strip(),
                    final=segment.final,
                    started_at_ms=segment.started_at_ms,
                    ended_at_ms=segment.ended_at_ms,
                    utterance_id=segment.utterance_id,
                    revision=segment.revision,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "realtime ASR transcript publication failed",
                extra={
                    "session_id": segment.session_id,
                    "audio_source": segment.source.value,
                    "final": segment.final,
                    "error_type": type(error).__name__,
                },
            )

    async def _schedule_observation(
        self,
        session_id: str,
        *,
        trigger_event_ids: tuple[str, ...] = (),
        trigger_frame_ids: tuple[str, ...] = (),
        target_viewer_id: str | None = None,
        target_persona_id: str | None = None,
        user_context: dict[str, str] | None = None,
        window_batch_tick: bool = False,
    ) -> bool:
        if not await self._session_tasks.accepts_results(session_id):
            return False
        window_batch_enabled, _ = await self._window_batch_settings(session_id)
        if window_batch_tick and not window_batch_enabled:
            return False
        if window_batch_enabled and not window_batch_tick:
            await self._accumulate_window_batch_delta(
                session_id,
                trigger_event_ids=trigger_event_ids,
                trigger_frame_ids=trigger_frame_ids,
                target_viewer_id=target_viewer_id,
                target_persona_id=target_persona_id,
                user_context=user_context,
            )
            return True
        if not window_batch_tick:
            await self._record_trigger(session_id)
        observation = await self._context_builder.build(
            session_id,
            user_context=user_context,
            trigger_event_ids=trigger_event_ids,
            trigger_frame_ids=trigger_frame_ids,
            target_viewer_id=target_viewer_id,
            target_persona_id=target_persona_id,
        )
        await self._scheduler.submit(observation)
        return True

    async def _schedule_screen_change_if_cold(
        self,
        session_id: str,
        *,
        frame_id: str,
        change_score: float | None,
    ) -> None:
        window_batch_enabled, _ = await self._window_batch_settings(session_id)
        change_threshold, cooldown_ms = await self._screen_trigger_policy(session_id)
        if window_batch_enabled:
            if change_score is not None and change_score >= change_threshold:
                await self._accumulate_window_batch_delta(
                    session_id,
                    trigger_frame_ids=(frame_id,),
                )
            return
        if change_score is None or change_score < change_threshold:
            return
        if not await self._claim_screen_trigger(session_id, cooldown_ms=cooldown_ms):
            return
        observation = await self._context_builder.build(
            session_id,
            trigger_frame_ids=(frame_id,),
        )
        await self._scheduler.submit(observation)

    async def _accumulate_window_batch_delta(
        self,
        session_id: str,
        *,
        trigger_event_ids: tuple[str, ...] = (),
        trigger_frame_ids: tuple[str, ...] = (),
        target_viewer_id: str | None = None,
        target_persona_id: str | None = None,
        user_context: dict[str, str] | None = None,
    ) -> None:
        async with self._lock:
            if self._active_session_id != session_id:
                return
            delta = self._window_batch_delta
            for event_id in trigger_event_ids:
                if event_id not in self._window_batch_processed_event_ids:
                    delta.event_ids[event_id] = None
            for frame_id in trigger_frame_ids:
                if frame_id not in self._window_batch_processed_frame_ids:
                    delta.frame_ids[frame_id] = None
            if target_viewer_id is not None:
                delta.targets.add(("viewer", target_viewer_id))
            if target_persona_id is not None:
                delta.targets.add(("persona", target_persona_id))
            if user_context is not None:
                delta.user_context.update(user_context)
            self._trim_ordered_ids(delta.event_ids)
            self._trim_ordered_ids(delta.frame_ids)

    async def _take_window_batch_delta(self, session_id: str) -> _WindowBatchDelta:
        async with self._lock:
            if self._active_session_id != session_id:
                return _WindowBatchDelta()
            delta = self._window_batch_delta
            self._window_batch_delta = _WindowBatchDelta()
            for event_id in delta.event_ids:
                self._remember_window_batch_id(
                    self._window_batch_processed_event_ids,
                    event_id,
                )
            for frame_id in delta.frame_ids:
                self._remember_window_batch_id(
                    self._window_batch_processed_frame_ids,
                    frame_id,
                )
            return delta

    async def _restore_window_batch_delta(
        self,
        session_id: str,
        delta: _WindowBatchDelta,
    ) -> None:
        async with self._lock:
            if self._active_session_id != session_id:
                return
            for event_id in delta.event_ids:
                self._window_batch_processed_event_ids.pop(event_id, None)
            for frame_id in delta.frame_ids:
                self._window_batch_processed_frame_ids.pop(frame_id, None)
            current = self._window_batch_delta
            restored = _WindowBatchDelta(
                event_ids=OrderedDict((*delta.event_ids.items(), *current.event_ids.items())),
                frame_ids=OrderedDict((*delta.frame_ids.items(), *current.frame_ids.items())),
                targets=delta.targets | current.targets,
                user_context={**delta.user_context, **current.user_context},
            )
            self._trim_ordered_ids(restored.event_ids)
            self._trim_ordered_ids(restored.frame_ids)
            self._window_batch_delta = restored

    async def _discard_window_batch_delta(self, session_id: str) -> None:
        async with self._lock:
            if self._active_session_id != session_id:
                return
            delta = self._window_batch_delta
            self._window_batch_delta = _WindowBatchDelta()
            for event_id in delta.event_ids:
                self._remember_window_batch_id(
                    self._window_batch_processed_event_ids,
                    event_id,
                )
            for frame_id in delta.frame_ids:
                self._remember_window_batch_id(
                    self._window_batch_processed_frame_ids,
                    frame_id,
                )

    @staticmethod
    def _window_batch_target(
        delta: _WindowBatchDelta,
    ) -> tuple[str | None, str | None]:
        if len(delta.targets) != 1:
            return None, None
        kind, target = next(iter(delta.targets))
        return (target, None) if kind == "viewer" else (None, target)

    def _remember_window_batch_id(
        self,
        ids: OrderedDict[str, None],
        value: str,
    ) -> None:
        ids[value] = None
        ids.move_to_end(value)
        self._trim_ordered_ids(ids)

    def _trim_ordered_ids(self, ids: OrderedDict[str, None]) -> None:
        while len(ids) > self._max_tracked_input_ids:
            ids.popitem(last=False)

    async def _screen_trigger_policy(self, session_id: str) -> tuple[float, int]:
        if self._screen_trigger_settings is None:
            return _SCREEN_CHANGE_TRIGGER_THRESHOLD, _SCREEN_TRIGGER_COOLDOWN_MS
        threshold, cooldown_ms = await self._screen_trigger_settings(session_id)
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0 <= threshold <= 1
        ):
            raise ValueError("screen trigger threshold must be between zero and one")
        if (
            isinstance(cooldown_ms, bool)
            or not isinstance(cooldown_ms, int)
            or cooldown_ms < 0
        ):
            raise ValueError("screen trigger cooldown must be a non-negative integer")
        return float(threshold), cooldown_ms

    async def _claim_screen_trigger(self, session_id: str, *, cooldown_ms: int) -> bool:
        now_ms = self._clock.now_ms()
        async with self._lock:
            self._require_active_locked(session_id)
            last_trigger_at_ms = self._last_trigger_at_ms
            if (
                last_trigger_at_ms is not None
                and now_ms < last_trigger_at_ms + cooldown_ms
            ):
                return False
            self._last_trigger_at_ms = now_ms
            return True

    async def _record_trigger(self, session_id: str) -> None:
        now_ms = self._clock.now_ms()
        async with self._lock:
            self._require_active_locked(session_id)
            self._last_trigger_at_ms = now_ms

    async def _reserve(
        self,
        *,
        session_id: str,
        input_id: str,
        kind: IngestInputKind,
        timestamp_ms: int,
        format_value: str,
        body_sha256: str,
        fingerprint: str,
    ) -> IngestReceipt | None:
        async with self._lock:
            self._require_active_locked(session_id)
            cached = self._cached_receipt_locked(
                input_id,
                IngestReceiptStage.RECEIVED,
                fingerprint,
            )
            if cached is not None:
                return cached
            last_timestamp = self._timestamp_for(kind)
            if last_timestamp is not None and timestamp_ms < last_timestamp:
                raise IngestInputOutOfOrderError(f"{kind.value} input is out of order")
            self._remember_locked(
                input_id,
                kind,
                timestamp_ms,
                format_value=format_value,
                body_sha256=body_sha256,
                fingerprint=fingerprint,
            )
            return None

    async def _reserve_audio(
        self,
        input: AudioInput,
        *,
        ended_at_ms: int,
        fingerprint: str,
        stage: IngestReceiptStage = IngestReceiptStage.RECEIVED,
    ) -> IngestReceipt | None:
        async with self._lock:
            self._require_active_locked(input.session_id)
            cached = self._cached_receipt_locked(input.input_id, stage, fingerprint)
            if cached is not None:
                return cached
            if input.source in self._pending_audio_ids:
                raise IngestInputOutOfOrderError("the previous audio input is not committed")
            last_audio_at_ms = self._timestamp_for(IngestInputKind.AUDIO, input.source)
            if last_audio_at_ms is not None and input.captured_at_ms < last_audio_at_ms:
                raise IngestInputOutOfOrderError("audio input is out of order")
            self._remember_locked(
                input.input_id,
                IngestInputKind.AUDIO,
                input.captured_at_ms,
                format_value=input.format,
                body_sha256=hashlib.sha256(input.body).hexdigest(),
                source=input.source,
                ended_at_ms=ended_at_ms,
                connection_id=input.connection_id,
                fingerprint=fingerprint,
                stage=stage,
            )
            self._pending_audio_ids[input.source] = input.input_id
            return None

    async def _release_audio(self, input_id: str, source: AudioSource) -> None:
        async with self._lock:
            if self._pending_audio_ids.get(source) == input_id:
                self._pending_audio_ids.pop(source, None)

    async def _settle(
        self,
        input_id: str,
        *,
        accepted: bool,
        receipt: IngestReceipt | None = None,
    ) -> None:
        async with self._lock:
            tracked = self._seen_inputs.get(input_id)
            if tracked is None:
                return
            if accepted:
                tracked.accepted = True
                if receipt is not None:
                    tracked.receipts[receipt.stage] = receipt
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

    def _remember_locked(
        self,
        input_id: str,
        kind: IngestInputKind,
        timestamp_ms: int,
        *,
        format_value: str,
        body_sha256: str,
        source: AudioSource | None = None,
        ended_at_ms: int | None = None,
        connection_id: str | None = None,
        fingerprint: str,
        stage: IngestReceiptStage = IngestReceiptStage.RECEIVED,
    ) -> None:
        self._seen_inputs[input_id] = _TrackedInput(
            kind=kind,
            timestamp_ms=timestamp_ms,
            format=format_value,
            body_sha256=body_sha256,
            source=source,
            ended_at_ms=ended_at_ms,
            connection_id=connection_id,
            fingerprints={stage: fingerprint},
        )
        while len(self._seen_inputs) > self._max_tracked_input_ids:
            evicted = next(
                (
                    (tracked_id, tracked)
                    for tracked_id, tracked in self._seen_inputs.items()
                    if tracked.accepted
                    and tracked_id not in self._pending_audio_ids.values()
                ),
                None,
            )
            if evicted is None:
                self._seen_inputs.pop(input_id, None)
                raise IngestCapacityExceededError("too many ingest inputs are in progress")
            evicted_id, tracked = evicted
            self._seen_inputs.pop(evicted_id)
            floor_key = (tracked.kind, tracked.source)
            current_floor = self._timestamp_floors.get(floor_key)
            if current_floor is None or tracked.timestamp_ms > current_floor:
                self._timestamp_floors[floor_key] = tracked.timestamp_ms

    def _cached_receipt_locked(
        self,
        input_id: str,
        stage: IngestReceiptStage,
        fingerprint: str,
    ) -> IngestReceipt | None:
        tracked = self._seen_inputs.get(input_id)
        if tracked is None:
            return None
        if tracked.fingerprints.get(stage) != fingerprint:
            raise DuplicateIngestInputError(input_id)
        receipt = tracked.receipts.get(stage)
        if receipt is None:
            raise IngestInputOutOfOrderError("the ingest input is still in progress")
        self._seen_inputs.move_to_end(input_id)
        return receipt

    def _timestamp_for(
        self,
        kind: IngestInputKind,
        source: AudioSource | None = None,
    ) -> int | None:
        timestamps = [
            tracked.timestamp_ms
            for tracked in self._seen_inputs.values()
            if tracked.kind is kind and tracked.source is source
        ]
        floor = self._timestamp_floors.get((kind, source))
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
    def _fingerprint(
        *,
        kind: IngestInputKind,
        source: AudioSource | None,
        timestamp_ms: int,
        format_value: str,
        body_sha256: str,
        stage: IngestReceiptStage,
    ) -> str:
        value = "\0".join(
            (
                kind.value,
                "" if source is None else source.value,
                str(timestamp_ms),
                format_value,
                body_sha256,
                stage.value,
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _input_fingerprint(
        cls,
        *,
        kind: IngestInputKind,
        source: AudioSource | None,
        timestamp_ms: int,
        format_value: str,
        body: bytes,
        stage: IngestReceiptStage,
    ) -> str:
        return cls._fingerprint(
            kind=kind,
            source=source,
            timestamp_ms=timestamp_ms,
            format_value=format_value,
            body_sha256=hashlib.sha256(body).hexdigest(),
            stage=stage,
        )

    @classmethod
    def _audio_fingerprint(
        cls,
        input: AudioInput,
        stage: IngestReceiptStage,
    ) -> str:
        format_value = input.format
        if stage is IngestReceiptStage.COMMITTED:
            format_value = (
                f"{format_value};turn_id={input.turn_id or ''};"
                f"system_audio_required={str(input.system_audio_required).lower()}"
            )
        return cls._input_fingerprint(
            kind=IngestInputKind.AUDIO,
            source=input.source,
            timestamp_ms=input.captured_at_ms,
            format_value=format_value,
            body=input.body,
            stage=stage,
        )

    @staticmethod
    def _text_format(input: TextInput) -> str:
        return (
            "text/plain;"
            f"viewer={input.target_viewer_id or ''};persona={input.target_persona_id or ''}"
        )

    async def _discard_provider_audio(self, source: AudioSource) -> None:
        discard = getattr(self._asr_provider, "discard", None)
        if discard is None:
            return
        try:
            await discard(source)
        except Exception as error:
            logger.warning(
                "failed to discard pending ASR audio",
                extra={
                    "audio_source": source.value,
                    "error_type": type(error).__name__,
                },
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
        self._seen_utterances.clear()
        self._partial_transcripts.clear()
        self._voice_turns.clear()
        for pending in self._pending_final_audio.values():
            pending.clear()
        self._coordinated_turns.clear()
        self._coordinated_utterance_keys.clear()
        self._timestamp_floors.clear()
        self._pending_audio_ids.clear()
        self._last_trigger_at_ms = None
        self._window_batch_delta = _WindowBatchDelta()
        self._window_batch_processed_event_ids.clear()
        self._window_batch_processed_frame_ids.clear()
