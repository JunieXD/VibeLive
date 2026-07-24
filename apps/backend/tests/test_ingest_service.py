import asyncio
from collections.abc import AsyncIterator

import pytest

from advx_backend.application.context_builder import ContextBuilder
from advx_backend.application.frame_store import FrameTooLargeError, InMemoryFrameStore
from advx_backend.application.ingest_service import (
    DuplicateIngestInputError,
    IngestCapacityExceededError,
    IngestInputOutOfOrderError,
    IngestService,
    IngestSessionNotActiveError,
)
from advx_backend.application.ports.asr import (
    AudioChunk,
    TranscriptSegment,
    TranscriptTargetResolution,
    TranscriptTargetResolver,
)
from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    FrameStoreLimits,
    TextInput,
)
from advx_backend.application.room_service import RoomService
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEventSource


class MutableClock:
    def __init__(self, now_ms: int = 1_000) -> None:
        self.value = now_ms

    def now_ms(self) -> int:
        self.value += 1
        return self.value


class SequenceIdGenerator:
    def __init__(self) -> None:
        self._next = 1

    def new_id(self) -> str:
        value = f"id-{self._next}"
        self._next += 1
        return value


class ActiveSessionScope:
    def __init__(self) -> None:
        self.active_session_id: str | None = "session-1"

    async def accepts_results(self, session_id: str) -> bool:
        return self.active_session_id == session_id

    async def start_task(self, *args: object, **kwargs: object) -> asyncio.Task[object]:
        raise AssertionError("IngestService does not register tasks through this fake")


class FakeAsrProvider:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.chunks: list[AudioChunk] = []
        self.commits = 0
        self.results_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self.fail_push = False

    async def start(self) -> None:
        self.started += 1

    async def push_audio(self, chunk: AudioChunk) -> None:
        if self.fail_push:
            raise RuntimeError("ASR unavailable")
        self.chunks.append(chunk)

    async def commit(self) -> None:
        self.commits += 1

    async def results(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            yield await self.results_queue.get()

    async def stop(self) -> None:
        self.stopped += 1


class RecordingScheduler:
    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self.cancelled: list[str] = []
        self.submitted = asyncio.Event()

    async def submit(self, observation: Observation) -> asyncio.Future[object | None]:
        self.observations.append(observation)
        self.submitted.set()
        future: asyncio.Future[object | None] = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    async def cancel_session(self, session_id: str) -> None:
        self.cancelled.append(session_id)


async def create_harness(
    *,
    max_tracked_input_ids: int = 1_024,
    voice_target_resolver: TranscriptTargetResolver | None = None,
) -> tuple[
    IngestService,
    RoomService,
    InMemoryFrameStore,
    FakeAsrProvider,
    RecordingScheduler,
]:
    clock = MutableClock()
    ids = SequenceIdGenerator()
    room = RoomService(clock=clock, id_generator=ids, event_capacity=16, event_ttl_ms=60_000)
    context = ContextBuilder(
        room_service=room,
        clock=clock,
        id_generator=ids,
        frame_capacity=4,
        frame_ttl_ms=60_000,
        max_frames_per_observation=4,
        max_events_per_observation=16,
    )
    frame_store = InMemoryFrameStore(
        limits=FrameStoreLimits(
            max_frames=4,
            max_frame_bytes=1_024,
            max_total_bytes=2_048,
        ),
        id_generator=ids,
    )
    asr = FakeAsrProvider()
    scheduler = RecordingScheduler()
    service = IngestService(
        room_service=room,
        context_builder=context,
        frame_store=frame_store,
        asr_provider=asr,
        scheduler=scheduler,
        session_tasks=ActiveSessionScope(),
        clock=clock,
        max_tracked_input_ids=max_tracked_input_ids,
        voice_target_resolver=voice_target_resolver,
    )
    await context.start_session("session-1")
    await service.start_session("session-1")
    return service, room, frame_store, asr, scheduler


@pytest.mark.asyncio
async def test_text_and_frame_inputs_build_observations_without_embedding_pixels() -> None:
    service, room, frame_store, asr, scheduler = await create_harness()
    await service.submit_text(
        TextInput(session_id="session-1", input_id="text-1", created_at_ms=100, text=" hello ")
    )
    await service.submit_frame(
        FrameInput(
            session_id="session-1",
            input_id="frame-1",
            captured_at_ms=200,
            mime_type="image/jpeg",
            body=b"pixels",
        )
    )

    events = await room.read_events("session-1")
    observation = scheduler.observations[-1]
    assert events[0].source_type is RoomEventSource.USER_TEXT
    assert events[0].text == "hello"
    assert observation.frames[0].data_ref.startswith("advx-frame:")
    assert b"pixels" not in repr(observation).encode()
    resolved = await frame_store.resolve(session_id="session-1", frame=observation.frames[0])
    assert resolved is not None and resolved.body == b"pixels"

    await service.stop_session("session-1")
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_text_input_persists_only_one_structured_target_in_event_payload() -> None:
    service, room, _, _, _ = await create_harness()

    await service.submit_text(
        TextInput(
            session_id="session-1",
            input_id="text-target",
            created_at_ms=100,
            text="@Viewer hello",
            target_viewer_id="viewer-1",
        )
    )

    events = await room.read_events("session-1")
    assert events[-1].payload["target_viewer_id"] == "viewer-1"
    assert "target_persona_id" not in events[-1].payload

    await service.stop_session("session-1")
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_audio_commit_only_publishes_final_transcript() -> None:
    service, room, frame_store, asr, scheduler = await create_harness()
    receipt = await service.submit_audio(
        AudioInput(
            session_id="session-1",
            input_id="audio-1",
            captured_at_ms=300,
            format="audio/pcm;rate=16000;channels=1;format=s16le",
            body=b"\x00\x00" * 160,
        )
    )
    committed = await service.commit_audio(
        AudioCommit(session_id="session-1", input_id="audio-1", committed_at_ms=320)
    )
    await asr.results_queue.put(
        TranscriptSegment(
            session_id="session-1",
            text="partial",
            started_at_ms=300,
            ended_at_ms=310,
            final=False,
        )
    )
    await asr.results_queue.put(
        TranscriptSegment(
            session_id="session-1",
            text="final words",
            started_at_ms=300,
            ended_at_ms=320,
            final=True,
        )
    )
    await asyncio.wait_for(scheduler.submitted.wait(), timeout=1)

    events = await room.read_events("session-1")
    assert receipt.stage.value == "received"
    assert committed.stage.value == "committed"
    assert asr.chunks[0].sample_rate == 16_000
    assert asr.commits == 1
    assert [event.text for event in events] == ["final words"]
    assert events[0].source_type is RoomEventSource.USER_VOICE

    await service.stop_session("session-1")
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_asr_commits_only_newer_successful_revisions_and_keeps_partial_state() -> None:
    service, room, _, asr, scheduler = await create_harness()
    await asr.results_queue.put(
        TranscriptSegment(
            session_id="session-1",
            text="draft",
            started_at_ms=10,
            ended_at_ms=20,
            final=False,
            utterance_id="utterance-1",
            revision=1,
        )
    )
    await asyncio.sleep(0)
    assert service.partial_transcript_snapshot("session-1").text == "draft"
    assert scheduler.observations == []

    for revision, text in ((1, "wrong"), (1, "duplicate"), (2, "corrected")):
        scheduler.submitted.clear()
        await asr.results_queue.put(
            TranscriptSegment(
                session_id="session-1",
                text=text,
                started_at_ms=10,
                ended_at_ms=20,
                final=True,
                utterance_id="utterance-1",
                revision=revision,
            )
        )
        if text != "duplicate":
            await asyncio.wait_for(scheduler.submitted.wait(), timeout=1)
        else:
            await asyncio.sleep(0.01)

    events = await room.read_events("session-1")
    assert [(event.text, event.payload["revision"]) for event in events] == [
        ("wrong", 1),
        ("corrected", 2),
    ]

    await service.stop_session("session-1")
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_failed_asr_revision_can_be_retried() -> None:
    service, room, _, _, scheduler = await create_harness()
    original = room.append_event
    calls = 0

    async def fail_once(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("write failed")
        return await original(*args, **kwargs)

    room.append_event = fail_once  # type: ignore[method-assign]
    segment = TranscriptSegment(
        session_id="session-1",
        text="retry me",
        started_at_ms=10,
        ended_at_ms=20,
        final=True,
        utterance_id="utterance-retry",
        revision=1,
    )

    with pytest.raises(RuntimeError, match="write failed"):
        await service._handle_transcript("session-1", segment)
    await service._handle_transcript("session-1", segment)

    events = await room.read_events("session-1")
    assert [event.text for event in events] == ["retry me"]
    assert len(scheduler.observations) == 1


@pytest.mark.asyncio
async def test_asr_consumer_retries_the_current_final_before_reading_next() -> None:
    service, room, _, asr, scheduler = await create_harness()
    original = room.append_event
    calls = 0

    async def fail_once(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient room write")
        return await original(*args, **kwargs)

    room.append_event = fail_once  # type: ignore[method-assign]
    await asr.results_queue.put(
        TranscriptSegment(
            session_id="session-1",
            text="consumer retry",
            started_at_ms=10,
            ended_at_ms=20,
            final=True,
            utterance_id="consumer-retry",
            revision=1,
        )
    )

    await asyncio.wait_for(scheduler.submitted.wait(), timeout=1)
    events = await room.read_events("session-1")

    assert calls == 2
    assert [event.text for event in events] == ["consumer retry"]
    assert len(scheduler.observations) == 1

    await service.stop_session("session-1")
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_final_voice_target_resolution_is_structured_and_traceable() -> None:
    class Resolver:
        async def resolve(
            self,
            segment: TranscriptSegment,
        ) -> TranscriptTargetResolution:
            assert segment.text == "Viewer answer me"
            return TranscriptTargetResolution(
                resolver_id="voice-mention-v1",
                target_viewer_id="viewer-1",
            )

    service, room, _, _, scheduler = await create_harness(
        voice_target_resolver=Resolver()
    )
    segment = TranscriptSegment(
        session_id="session-1",
        text="Viewer answer me",
        started_at_ms=10,
        ended_at_ms=20,
        final=True,
        utterance_id="voice-target",
        revision=1,
    )

    await service._handle_transcript("session-1", segment)

    event = (await room.read_events("session-1"))[-1]
    assert event.payload["target_viewer_id"] == "viewer-1"
    assert event.payload["target_resolver_id"] == "voice-mention-v1"
    observation = scheduler.observations[-1]
    assert observation.target_viewer_id == "viewer-1"
    assert observation.trigger_event_ids == (event.event_id,)


@pytest.mark.asyncio
async def test_ingest_rejects_duplicate_out_of_order_and_stopped_session() -> None:
    service, room, frame_store, asr, scheduler = await create_harness()
    first = TextInput(session_id="session-1", input_id="text-1", created_at_ms=100, text="one")
    await service.submit_text(first)
    with pytest.raises(DuplicateIngestInputError):
        await service.submit_text(first)
    with pytest.raises(IngestInputOutOfOrderError):
        await service.submit_text(
            TextInput(session_id="session-1", input_id="text-2", created_at_ms=99, text="old")
        )

    await service.stop_session("session-1")
    assert asr.stopped == 1
    assert scheduler.cancelled == ["session-1"]
    with pytest.raises(IngestSessionNotActiveError):
        await service.submit_text(
            TextInput(session_id="session-1", input_id="text-3", created_at_ms=101, text="late")
        )
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_failed_frame_reservation_does_not_advance_ordering() -> None:
    service, room, frame_store, asr, scheduler = await create_harness()

    with pytest.raises(FrameTooLargeError, match="frame body"):
        await service.submit_frame(
            FrameInput(
                session_id="session-1",
                input_id="frame-failed",
                captured_at_ms=200,
                mime_type="image/jpeg",
                body=b"x" * 1_025,
            )
        )

    receipt = await service.submit_frame(
        FrameInput(
            session_id="session-1",
            input_id="frame-retry",
            captured_at_ms=100,
            mime_type="image/jpeg",
            body=b"pixels",
        )
    )

    assert receipt.input_id == "frame-retry"
    await service.stop_session("session-1")
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_failed_audio_reservation_does_not_advance_ordering() -> None:
    service, room, frame_store, asr, scheduler = await create_harness()
    asr.fail_push = True

    with pytest.raises(RuntimeError, match="ASR unavailable"):
        await service.submit_audio(
            AudioInput(
                session_id="session-1",
                input_id="audio-failed",
                captured_at_ms=200,
                format="audio/pcm;rate=16000;channels=1;format=s16le",
                body=b"\x00\x00",
            )
        )

    asr.fail_push = False
    receipt = await service.submit_audio(
        AudioInput(
            session_id="session-1",
            input_id="audio-retry",
            captured_at_ms=100,
            format="audio/pcm;rate=16000;channels=1;format=s16le",
            body=b"\x00\x00",
        )
    )

    assert receipt.input_id == "audio-retry"
    await service.stop_session("session-1")
    await room.stop_session("session-1")


@pytest.mark.asyncio
async def test_inflight_inputs_stay_bounded_and_failed_timestamp_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, room, frame_store, asr, scheduler = await create_harness(max_tracked_input_ids=1)
    original_append_event = room.append_event
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def append_event(session_id: str, **kwargs: object):
        payload = kwargs.get("payload")
        if isinstance(payload, dict) and payload.get("input_id") == "text-failed":
            blocked.set()
            await release.wait()
            raise RuntimeError("room unavailable")
        return await original_append_event(session_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(room, "append_event", append_event)
    failed = asyncio.create_task(
        service.submit_text(
            TextInput(
                session_id="session-1",
                input_id="text-failed",
                created_at_ms=200,
                text="failed",
            )
        )
    )
    await blocked.wait()

    with pytest.raises(IngestCapacityExceededError):
        await service.submit_text(
            TextInput(
                session_id="session-1",
                input_id="text-busy",
                created_at_ms=300,
                text="busy",
            )
        )

    release.set()
    with pytest.raises(RuntimeError, match="room unavailable"):
        await failed

    receipt = await service.submit_text(
        TextInput(
            session_id="session-1",
            input_id="text-retry",
            created_at_ms=100,
            text="retry",
        )
    )
    assert receipt.input_id == "text-retry"

    await service.stop_session("session-1")
    await room.stop_session("session-1")
