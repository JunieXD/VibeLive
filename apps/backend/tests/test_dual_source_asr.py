import asyncio
from types import SimpleNamespace

import pytest

from advx_backend.application.ingest_service import (
    DuplicateIngestInputError,
    IngestInputOutOfOrderError,
    IngestService,
)
from advx_backend.application.ports.asr import AudioChunk, AudioSource, TranscriptSegment
from advx_backend.application.ports.ingest import AudioCommit, AudioInput
from advx_backend.application.recorded_scenario import _RecordedAsrProvider
from advx_backend.contracts.realtime import AsrTranscriptEvent
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEventSource
from advx_backend.providers.asr.mux import AsrProviderMux

AUDIO_FORMAT = "audio/pcm;rate=16000;channels=1;format=s16le"


class _Clock:
    def __init__(self, now_ms: int = 10_000) -> None:
        self._now_ms = now_ms

    def now_ms(self) -> int:
        return self._now_ms


class _SessionTasks:
    async def accepts_results(self, session_id: str) -> bool:
        return session_id == "session"


class _Asr:
    def __init__(self) -> None:
        self.chunks: list[AudioChunk] = []
        self.commits: list[AudioSource] = []
        self.discards: list[AudioSource] = []

    async def push_audio(self, chunk: AudioChunk) -> None:
        self.chunks.append(chunk)

    async def commit(self, source: AudioSource = AudioSource.MICROPHONE) -> None:
        self.commits.append(source)

    async def discard(self, source: AudioSource = AudioSource.MICROPHONE) -> None:
        self.discards.append(source)


class _Room:
    def __init__(self) -> None:
        self.events: list[SimpleNamespace] = []

    async def append_event(self, session_id: str, **values: object) -> SimpleNamespace:
        event = SimpleNamespace(event_id=f"voice-{len(self.events) + 1}", **values)
        self.events.append(event)
        return event


class _Context:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def build(self, session_id: str, **values: object) -> Observation:
        self.calls.append(values)
        return Observation(
            session_id=session_id,
            observation_id=f"observation-{len(self.calls)}",
            created_at_ms=10_000,
        )


class _Scheduler:
    def __init__(self) -> None:
        self.observations: list[Observation] = []

    async def submit(self, observation: Observation) -> asyncio.Future[None]:
        self.observations.append(observation)
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future


class _Publisher:
    def __init__(self) -> None:
        self.events: list[AsrTranscriptEvent] = []

    async def publish_transcript(self, event: AsrTranscriptEvent) -> None:
        self.events.append(event)


class _FailingPublisher:
    async def publish_transcript(self, event: AsrTranscriptEvent) -> None:
        raise RuntimeError("realtime unavailable")


def _ingest(
    *,
    asr: object,
    room: _Room | None = None,
    context: _Context | None = None,
    scheduler: _Scheduler | None = None,
    publisher: _Publisher | None = None,
    clock: _Clock | None = None,
    voice_turn_silence_ms: int = 1,
    coordinated_turn_timeout_ms: int = 50,
) -> IngestService:
    service = IngestService(
        room_service=room or _Room(),
        context_builder=context or _Context(),
        frame_store=object(),
        asr_provider=asr,
        scheduler=scheduler or _Scheduler(),
        session_tasks=_SessionTasks(),
        clock=clock or _Clock(),
        voice_turn_silence_ms=voice_turn_silence_ms,
        coordinated_turn_timeout_ms=coordinated_turn_timeout_ms,
        transcript_publisher=publisher,
    )
    service._active_session_id = "session"
    return service


@pytest.mark.asyncio
async def test_audio_pending_commit_and_ordering_are_isolated_by_source() -> None:
    asr = _Asr()
    ingest = _ingest(asr=asr)

    await ingest.submit_audio(
        AudioInput(
            session_id="session",
            input_id="mic-1",
            captured_at_ms=100,
            format=AUDIO_FORMAT,
            body=b"\x00\x00",
        )
    )
    await ingest.submit_audio(
        AudioInput(
            session_id="session",
            input_id="system-1",
            captured_at_ms=50,
            format=AUDIO_FORMAT,
            body=b"\x00\x00",
            source=AudioSource.SYSTEM_AUDIO,
        )
    )
    with pytest.raises(IngestInputOutOfOrderError):
        await ingest.submit_audio(
            AudioInput(
                session_id="session",
                input_id="mic-2",
                captured_at_ms=101,
                format=AUDIO_FORMAT,
                body=b"\x00\x00",
            )
        )

    await ingest.commit_audio(
        AudioCommit(session_id="session", input_id="system-1", committed_at_ms=51,
                    source=AudioSource.SYSTEM_AUDIO)
    )
    await ingest.commit_audio(
        AudioCommit(session_id="session", input_id="mic-1", committed_at_ms=101)
    )

    assert [chunk.source for chunk in asr.chunks] == [
        AudioSource.MICROPHONE,
        AudioSource.SYSTEM_AUDIO,
    ]
    assert asr.commits == [AudioSource.SYSTEM_AUDIO, AudioSource.MICROPHONE]


@pytest.mark.asyncio
async def test_transcripts_room_events_and_realtime_are_source_isolated() -> None:
    room = _Room()
    context = _Context()
    scheduler = _Scheduler()
    publisher = _Publisher()
    ingest = _ingest(
        asr=object(),
        room=room,
        context=context,
        scheduler=scheduler,
        publisher=publisher,
    )

    for source, text in (
        (AudioSource.MICROPHONE, "host"),
        (AudioSource.SYSTEM_AUDIO, "game"),
    ):
        await ingest._handle_transcript(
            "session",
            TranscriptSegment(
                session_id="session",
                source=source,
                text=f"{text} partial",
                started_at_ms=0,
                ended_at_ms=0,
                final=False,
                utterance_id="shared",
            ),
        )
        await ingest._handle_transcript(
            "session",
            TranscriptSegment(
                session_id="session",
                source=source,
                text=text,
                started_at_ms=0,
                ended_at_ms=0,
                final=True,
                utterance_id="shared",
            ),
        )

    await asyncio.sleep(0.01)

    assert [event.source_id for event in room.events] == ["host", "system-audio"]
    assert [event.source_type for event in room.events] == [
        RoomEventSource.USER_VOICE,
        RoomEventSource.SYSTEM_EVENT,
    ]
    assert [event.payload["audio_source"] for event in room.events] == [
        "microphone",
        "system_audio",
    ]
    assert len(scheduler.observations) == 1
    assert [event.final for event in publisher.events] == [False, True, False, True]
    assert ingest.partial_transcript_snapshot("session") is None
    assert (
        ingest.partial_transcript_snapshot("session", AudioSource.SYSTEM_AUDIO) is None
    )


async def _commit_audio(
    ingest: IngestService,
    *,
    input_id: str,
    source: AudioSource,
    turn_id: str,
    captured_at_ms: int,
    system_audio_required: bool = False,
) -> None:
    await ingest.submit_audio(
        AudioInput(
            session_id="session",
            input_id=input_id,
            captured_at_ms=captured_at_ms,
            format=AUDIO_FORMAT,
            body=b"\x00\x00" * 160,
            source=source,
        )
    )
    await ingest.commit_audio(
        AudioCommit(
            session_id="session",
            input_id=input_id,
            committed_at_ms=captured_at_ms + 10,
            source=source,
            turn_id=turn_id,
            system_audio_required=system_audio_required,
        )
    )


@pytest.mark.asyncio
async def test_coordinated_turn_waits_for_system_final_and_schedules_once() -> None:
    room = _Room()
    context = _Context()
    scheduler = _Scheduler()
    ingest = _ingest(asr=_Asr(), room=room, context=context, scheduler=scheduler)

    await _commit_audio(
        ingest,
        input_id="system-1",
        source=AudioSource.SYSTEM_AUDIO,
        turn_id="turn-1",
        captured_at_ms=100,
    )
    await _commit_audio(
        ingest,
        input_id="mic-1",
        source=AudioSource.MICROPHONE,
        turn_id="turn-1",
        captured_at_ms=200,
        system_audio_required=True,
    )

    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.MICROPHONE,
            text="主播的问题",
            started_at_ms=200,
            ended_at_ms=210,
            final=True,
            utterance_id="mic-1",
        ),
    )
    await asyncio.sleep(0.01)

    assert [event.text for event in room.events] == ["主播的问题"]
    assert scheduler.observations == []

    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="视频里的对白",
            started_at_ms=100,
            ended_at_ms=110,
            final=True,
            utterance_id="system-1",
        ),
    )
    await asyncio.sleep(0.01)

    assert [event.source_id for event in room.events] == ["host", "system-audio"]
    assert len(scheduler.observations) == 1
    assert context.calls[0]["trigger_event_ids"] == ("voice-1", "voice-2")


@pytest.mark.asyncio
async def test_system_final_never_schedules_without_a_microphone_turn() -> None:
    room = _Room()
    scheduler = _Scheduler()
    ingest = _ingest(
        asr=_Asr(),
        room=room,
        scheduler=scheduler,
        coordinated_turn_timeout_ms=5,
    )

    await _commit_audio(
        ingest,
        input_id="system-1",
        source=AudioSource.SYSTEM_AUDIO,
        turn_id="turn-1",
        captured_at_ms=100,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="视频里的对白",
            started_at_ms=100,
            ended_at_ms=110,
            final=True,
            utterance_id="system-1",
        ),
    )
    await asyncio.sleep(0.02)

    assert [event.text for event in room.events] == ["视频里的对白"]
    assert scheduler.observations == []


@pytest.mark.asyncio
async def test_empty_system_final_completes_a_required_turn() -> None:
    room = _Room()
    context = _Context()
    scheduler = _Scheduler()
    ingest = _ingest(asr=_Asr(), room=room, context=context, scheduler=scheduler)

    await _commit_audio(
        ingest,
        input_id="system-1",
        source=AudioSource.SYSTEM_AUDIO,
        turn_id="turn-1",
        captured_at_ms=100,
    )
    await _commit_audio(
        ingest,
        input_id="mic-1",
        source=AudioSource.MICROPHONE,
        turn_id="turn-1",
        captured_at_ms=200,
        system_audio_required=True,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.MICROPHONE,
            text="主播的问题",
            started_at_ms=200,
            ended_at_ms=210,
            final=True,
            utterance_id="mic-1",
        ),
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="   ",
            started_at_ms=100,
            ended_at_ms=110,
            final=True,
            utterance_id="system-1",
        ),
    )
    await asyncio.sleep(0.01)

    assert [event.text for event in room.events] == ["主播的问题"]
    assert len(scheduler.observations) == 1
    assert context.calls[0]["trigger_event_ids"] == ("voice-1",)


@pytest.mark.asyncio
async def test_empty_microphone_final_never_schedules_ai() -> None:
    room = _Room()
    scheduler = _Scheduler()
    ingest = _ingest(asr=_Asr(), room=room, scheduler=scheduler)

    await _commit_audio(
        ingest,
        input_id="mic-1",
        source=AudioSource.MICROPHONE,
        turn_id="turn-1",
        captured_at_ms=200,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.MICROPHONE,
            text="",
            started_at_ms=200,
            ended_at_ms=210,
            final=True,
            utterance_id="mic-1",
        ),
    )
    await asyncio.sleep(0.01)

    assert room.events == []
    assert scheduler.observations == []


@pytest.mark.asyncio
async def test_required_system_audio_timeout_degrades_and_late_system_only_persists() -> None:
    room = _Room()
    context = _Context()
    scheduler = _Scheduler()
    ingest = _ingest(
        asr=_Asr(),
        room=room,
        context=context,
        scheduler=scheduler,
        coordinated_turn_timeout_ms=5,
    )

    await _commit_audio(
        ingest,
        input_id="mic-1",
        source=AudioSource.MICROPHONE,
        turn_id="turn-1",
        captured_at_ms=200,
        system_audio_required=True,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.MICROPHONE,
            text="主播的问题",
            started_at_ms=200,
            ended_at_ms=210,
            final=True,
            utterance_id="mic-1",
        ),
    )
    await asyncio.sleep(0.02)

    assert [event.text for event in room.events] == ["主播的问题"]
    assert len(scheduler.observations) == 1
    assert context.calls[0]["user_context"] == {
        "turn_id": "turn-1",
        "system_audio_degraded": "true",
    }

    await _commit_audio(
        ingest,
        input_id="system-1",
        source=AudioSource.SYSTEM_AUDIO,
        turn_id="turn-1",
        captured_at_ms=300,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="迟到的系统声音",
            started_at_ms=300,
            ended_at_ms=310,
            final=True,
            utterance_id="system-1",
        ),
    )
    await asyncio.sleep(0.01)

    assert [event.text for event in room.events] == ["主播的问题", "迟到的系统声音"]
    assert len(scheduler.observations) == 1


@pytest.mark.asyncio
async def test_atomic_audio_is_idempotent_and_rejects_same_id_with_new_content() -> None:
    asr = _Asr()
    ingest = _ingest(asr=asr)
    input = AudioInput(
        session_id="session",
        input_id="audio-atomic",
        captured_at_ms=100,
        format=AUDIO_FORMAT,
        body=b"\x00\x00",
        turn_id="turn-atomic",
        connection_id="connection-1",
    )

    first = await ingest.submit_audio_and_commit(input)
    duplicate = await ingest.submit_audio_and_commit(input)

    assert duplicate == first
    assert asr.commits == [AudioSource.MICROPHONE]
    assert len(asr.chunks) == 1
    with pytest.raises(DuplicateIngestInputError):
        await ingest.submit_audio_and_commit(
            AudioInput(
                session_id="session",
                input_id="audio-atomic",
                captured_at_ms=100,
                format=AUDIO_FORMAT,
                body=b"\x01\x00",
                turn_id="turn-atomic",
                connection_id="connection-1",
            )
        )


@pytest.mark.asyncio
async def test_disconnect_clears_connection_owned_pending_audio() -> None:
    asr = _Asr()
    ingest = _ingest(asr=asr)
    await ingest.submit_audio(
        AudioInput(
            session_id="session",
            input_id="lost-audio",
            captured_at_ms=100,
            format=AUDIO_FORMAT,
            body=b"\x00\x00",
            connection_id="connection-1",
        )
    )

    await ingest.clear_connection("connection-1")
    receipt = await ingest.submit_audio(
        AudioInput(
            session_id="session",
            input_id="next-audio",
            captured_at_ms=101,
            format=AUDIO_FORMAT,
            body=b"\x00\x00",
            connection_id="connection-2",
        )
    )

    assert receipt.input_id == "next-audio"
    assert asr.discards == [AudioSource.MICROPHONE]


@pytest.mark.asyncio
async def test_missing_microphone_final_never_schedules_ai() -> None:
    room = _Room()
    scheduler = _Scheduler()
    ingest = _ingest(
        asr=_Asr(),
        room=room,
        scheduler=scheduler,
        coordinated_turn_timeout_ms=5,
    )

    await _commit_audio(
        ingest,
        input_id="system-1",
        source=AudioSource.SYSTEM_AUDIO,
        turn_id="turn-1",
        captured_at_ms=100,
    )
    await _commit_audio(
        ingest,
        input_id="mic-1",
        source=AudioSource.MICROPHONE,
        turn_id="turn-1",
        captured_at_ms=200,
        system_audio_required=True,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="视频里的对白",
            started_at_ms=100,
            ended_at_ms=110,
            final=True,
            utterance_id="system-1",
        ),
    )
    await asyncio.sleep(0.02)

    assert [event.text for event in room.events] == ["视频里的对白"]
    assert scheduler.observations == []


@pytest.mark.asyncio
async def test_timed_out_turn_does_not_block_the_next_final_transcript() -> None:
    scheduler = _Scheduler()
    ingest = _ingest(
        asr=_Asr(),
        scheduler=scheduler,
        coordinated_turn_timeout_ms=5,
    )

    await _commit_audio(
        ingest,
        input_id="mic-1",
        source=AudioSource.MICROPHONE,
        turn_id="turn-1",
        captured_at_ms=100,
        system_audio_required=True,
    )
    await asyncio.sleep(0.02)
    await _commit_audio(
        ingest,
        input_id="mic-2",
        source=AudioSource.MICROPHONE,
        turn_id="turn-2",
        captured_at_ms=200,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.MICROPHONE,
            text="下一句正常返回",
            started_at_ms=200,
            ended_at_ms=210,
            final=True,
            utterance_id="mic-2",
        ),
    )
    await asyncio.sleep(0.01)

    assert len(scheduler.observations) == 1


@pytest.mark.asyncio
async def test_coordinated_turn_waits_until_the_microphone_delay_has_elapsed() -> None:
    room = _Room()
    scheduler = _Scheduler()
    ingest = _ingest(
        asr=_Asr(),
        room=room,
        scheduler=scheduler,
        clock=_Clock(1_000),
        voice_turn_silence_ms=30,
    )

    await _commit_audio(
        ingest,
        input_id="mic-1",
        source=AudioSource.MICROPHONE,
        turn_id="turn-1",
        captured_at_ms=1_000,
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            source=AudioSource.MICROPHONE,
            text="主播的问题",
            started_at_ms=1_000,
            ended_at_ms=1_010,
            final=True,
            utterance_id="mic-1",
        ),
    )
    await asyncio.sleep(0.01)
    assert scheduler.observations == []

    await asyncio.sleep(0.04)
    assert len(scheduler.observations) == 1


@pytest.mark.asyncio
async def test_final_transcript_schedules_ai_when_realtime_publication_fails() -> None:
    room = _Room()
    scheduler = _Scheduler()
    ingest = _ingest(
        asr=object(),
        room=room,
        scheduler=scheduler,
        publisher=_FailingPublisher(),  # type: ignore[arg-type]
    )

    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            text="durable voice",
            started_at_ms=0,
            ended_at_ms=0,
            final=True,
        ),
    )
    await asyncio.sleep(0.01)

    assert [event.text for event in room.events] == ["durable voice"]
    assert len(scheduler.observations) == 1


@pytest.mark.asyncio
async def test_oversized_transcript_is_rejected_before_room_side_effects() -> None:
    room = _Room()
    scheduler = _Scheduler()
    publisher = _Publisher()
    ingest = _ingest(
        asr=object(),
        room=room,
        scheduler=scheduler,
        publisher=publisher,
    )

    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            text="x" * 4_001,
            started_at_ms=0,
            ended_at_ms=0,
            final=True,
        ),
    )

    assert room.events == []
    assert scheduler.observations == []
    assert publisher.events == []


class _MuxProvider:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        results_end: bool = False,
        results_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.fail_commit = fail_commit
        self.results_end = results_end
        self.results_error = results_error
        self.stop_error = stop_error
        self.queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self.stopped = False

    async def start(self) -> None:
        pass

    async def push_audio(self, chunk: AudioChunk) -> None:
        pass

    async def commit(self, source: AudioSource = AudioSource.MICROPHONE) -> None:
        if self.fail_commit:
            raise RuntimeError("source failed")

    async def results(self):
        if self.results_error is not None:
            error = self.results_error
            self.results_error = None
            raise error
        if self.results_end:
            return
        while True:
            yield await self.queue.get()

    async def stop(self) -> None:
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error


def test_asr_mux_rejects_shared_provider_instance() -> None:
    provider = _MuxProvider()
    with pytest.raises(ValueError, match="independent provider instances"):
        AsrProviderMux(
            {
                AudioSource.MICROPHONE: provider,
                AudioSource.SYSTEM_AUDIO: provider,
            }
        )


@pytest.mark.asyncio
async def test_asr_mux_keeps_other_source_alive_after_one_source_fails() -> None:
    microphone = _MuxProvider(fail_commit=True)
    system_audio = _MuxProvider()
    mux = AsrProviderMux(
        {
            AudioSource.MICROPHONE: microphone,
            AudioSource.SYSTEM_AUDIO: system_audio,
        }
    )
    await mux.start()

    with pytest.raises(RuntimeError, match="source failed"):
        await mux.commit(AudioSource.MICROPHONE)
    await mux.commit(AudioSource.SYSTEM_AUDIO)
    await system_audio.queue.put(
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="still alive",
            started_at_ms=0,
            ended_at_ms=1,
            final=True,
        )
    )

    results = mux.results()
    result = await asyncio.wait_for(anext(results), timeout=1)
    await mux.stop()

    assert result.source is AudioSource.SYSTEM_AUDIO
    assert result.text == "still alive"


@pytest.mark.asyncio
async def test_asr_mux_degrades_ended_source_without_killing_other_source(
) -> None:
    failed_provider = _MuxProvider(results_end=True)
    system_audio = _MuxProvider()
    mux = AsrProviderMux(
        {
            AudioSource.MICROPHONE: failed_provider,
            AudioSource.SYSTEM_AUDIO: system_audio,
        }
    )
    await mux.start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="unavailable"):
        await mux.commit(AudioSource.MICROPHONE)
    with pytest.raises(RuntimeError, match="unavailable"):
        await mux.push_audio(
            AudioChunk(
                session_id="session",
                source=AudioSource.MICROPHONE,
                started_at_ms=0,
                ended_at_ms=1,
                sample_rate=16_000,
                channels=1,
                sample_width_bits=16,
                pcm=b"\x00\x00",
            )
        )
    await system_audio.queue.put(
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="other source",
            started_at_ms=0,
            ended_at_ms=1,
            final=True,
        )
    )
    result = await asyncio.wait_for(anext(mux.results()), timeout=1)
    await mux.stop()

    assert result.source is AudioSource.SYSTEM_AUDIO


@pytest.mark.asyncio
async def test_asr_mux_reenters_results_after_transient_segment_error() -> None:
    microphone = _MuxProvider(results_error=RuntimeError("segment failed"))
    system_audio = _MuxProvider()
    mux = AsrProviderMux(
        {
            AudioSource.MICROPHONE: microphone,
            AudioSource.SYSTEM_AUDIO: system_audio,
        }
    )
    await mux.start()
    await microphone.queue.put(
        TranscriptSegment(
            session_id="session",
            source=AudioSource.MICROPHONE,
            text="recovered",
            started_at_ms=0,
            ended_at_ms=1,
            final=True,
        )
    )
    await system_audio.queue.put(
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="other source",
            started_at_ms=0,
            ended_at_ms=1,
            final=True,
        )
    )

    results = mux.results()
    received = {
        (await asyncio.wait_for(anext(results), timeout=1)).source,
        (await asyncio.wait_for(anext(results), timeout=1)).source,
    }
    await mux.commit(AudioSource.MICROPHONE)
    await mux.stop()

    assert received == set(AudioSource)


@pytest.mark.asyncio
async def test_asr_mux_rejects_mislabeled_result_without_harming_other_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    microphone = _MuxProvider()
    system_audio = _MuxProvider()
    mux = AsrProviderMux(
        {
            AudioSource.MICROPHONE: microphone,
            AudioSource.SYSTEM_AUDIO: system_audio,
        }
    )
    await mux.start()
    await microphone.queue.put(
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="mislabeled",
            started_at_ms=0,
            ended_at_ms=1,
            final=True,
        )
    )
    await system_audio.queue.put(
        TranscriptSegment(
            session_id="session",
            source=AudioSource.SYSTEM_AUDIO,
            text="valid",
            started_at_ms=0,
            ended_at_ms=1,
            final=True,
        )
    )

    result = await asyncio.wait_for(anext(mux.results()), timeout=1)
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="unavailable"):
        await mux.commit(AudioSource.MICROPHONE)
    await mux.stop()

    assert result.text == "valid"
    assert "mismatched audio source" in caplog.text


@pytest.mark.asyncio
async def test_asr_mux_stop_cleans_every_provider_then_propagates_error() -> None:
    microphone = _MuxProvider(stop_error=RuntimeError("microphone stop failed"))
    system_audio = _MuxProvider()
    mux = AsrProviderMux(
        {
            AudioSource.MICROPHONE: microphone,
            AudioSource.SYSTEM_AUDIO: system_audio,
        }
    )
    await mux.start()
    results = mux.results()

    with pytest.raises(RuntimeError, match="microphone stop failed"):
        await mux.stop()

    assert microphone.stopped
    assert system_audio.stopped
    with pytest.raises(StopAsyncIteration):
        await anext(results)


class _RecordedLedger:
    def __init__(self) -> None:
        self.outputs = [
            {"text": "system"},
            {"text": "microphone"},
        ]

    def consume(self, provider_role: str) -> dict[str, object]:
        assert provider_role == "asr"
        return self.outputs.pop(0)


@pytest.mark.asyncio
async def test_recorded_asr_preserves_interleaved_audio_sources() -> None:
    ledger = _RecordedLedger()
    delivered = asyncio.Event()
    providers = {
        source: _RecordedAsrProvider(
            ledger,  # type: ignore[arg-type]
            source=source,
            final_delivered=delivered,
        )
        for source in AudioSource
    }
    mux = AsrProviderMux(providers)
    await mux.start()
    chunks = {
        source: AudioChunk(
            session_id="session",
            source=source,
            started_at_ms=index,
            ended_at_ms=index + 1,
            sample_rate=16_000,
            channels=1,
            sample_width_bits=16,
            pcm=b"\x00\x00",
        )
        for index, source in enumerate(AudioSource)
    }
    await mux.push_audio(chunks[AudioSource.MICROPHONE])
    await mux.push_audio(chunks[AudioSource.SYSTEM_AUDIO])
    await mux.commit(AudioSource.SYSTEM_AUDIO)
    await mux.commit(AudioSource.MICROPHONE)

    results = mux.results()
    received = [
        await asyncio.wait_for(anext(results), timeout=1),
        await asyncio.wait_for(anext(results), timeout=1),
    ]
    await mux.stop()

    assert {segment.source: segment.text for segment in received} == {
        AudioSource.SYSTEM_AUDIO: "system",
        AudioSource.MICROPHONE: "microphone",
    }
