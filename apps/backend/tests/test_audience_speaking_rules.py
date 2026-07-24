import asyncio

import pytest

from advx_backend.application.ingest_service import IngestService
from advx_backend.application.observation_wave_builder import select_frame_bundle
from advx_backend.application.ports.asr import TranscriptSegment
from advx_backend.application.reaction_scheduler import (
    LatestWinsReactionScheduler,
    ReactionSchedulerConfig,
)
from advx_backend.application.reaction_service import ReactionResult
from advx_backend.application.viewer_barrage_pipeline import ViewerBarragePipeline
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
)
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation import Observation
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    FrameBundleSettings,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.room import RoomEvent
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstanceVariant, ViewerPrivateState


class _Clock:
    def now_ms(self) -> int:
        return 1_000


class _SessionTasks:
    async def start_task(self, session_id, factory, *, name=None):
        del session_id
        return asyncio.create_task(factory(), name=name)

    async def accepts_results(self, session_id: str) -> bool:
        del session_id
        return True


class _Executor:
    def __init__(self) -> None:
        self.observation_ids: list[str] = []

    async def react(self, observation: Observation) -> ReactionResult:
        self.observation_ids.append(observation.observation_id)
        await asyncio.sleep(0)
        return ReactionResult(published_events=(), validations=())


class _BlockingExecutor:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.release = asyncio.Event()

    async def react(self, observation: Observation) -> ReactionResult:
        self.started.append(observation.observation_id)
        await self.release.wait()
        return ReactionResult(published_events=(), validations=())


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"id-{self.value}"


class _VoiceClock:
    def now_ms(self) -> int:
        return 4_000


class _VoiceRoom:
    def __init__(self) -> None:
        self.events: list[RoomEvent] = []

    async def append_event(self, session_id: str, **values: object) -> RoomEvent:
        event = RoomEvent(
            event_id=f"voice-{len(self.events) + 1}",
            session_id=session_id,
            sequence=len(self.events) + 1,
            source_type=values["source_type"],
            created_at_ms=3_000,
            source_id=values.get("source_id"),
            text=values.get("text"),
            payload=values.get("payload", {}),
        )
        self.events.append(event)
        return event


class _VoiceContextBuilder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def build(self, session_id: str, **values: object) -> Observation:
        self.calls.append({"session_id": session_id, **values})
        return Observation(
            session_id=session_id,
            observation_id=f"voice-observation-{len(self.calls)}",
            created_at_ms=3_000,
        )


class _VoiceScheduler:
    def __init__(self) -> None:
        self.observations: list[Observation] = []

    async def submit(self, observation: Observation) -> None:
        self.observations.append(observation)

    async def cancel_session(self, session_id: str) -> None:
        del session_id


@pytest.mark.asyncio
async def test_user_observations_are_processed_in_order_without_replacement() -> None:
    executor = _Executor()
    scheduler = LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=_SessionTasks(),
        clock=_Clock(),
        config=ReactionSchedulerConfig(
            observation_ttl_ms=10_000,
            max_pending_observations_per_session=8,
        ),
    )
    first = Observation(session_id="session", observation_id="first", created_at_ms=1_000)
    second = Observation(session_id="session", observation_id="second", created_at_ms=1_000)

    first_result = await scheduler.submit(first)
    second_result = await scheduler.submit(second)

    assert await first_result is not None
    assert await second_result is not None
    assert executor.observation_ids == ["first", "second"]


@pytest.mark.asyncio
async def test_new_input_starts_without_waiting_for_an_earlier_wave() -> None:
    executor = _BlockingExecutor()
    scheduler = LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=_SessionTasks(),
        clock=_Clock(),
    )
    first = await scheduler.submit(
        Observation(session_id="session", observation_id="first", created_at_ms=1_000)
    )
    second = await scheduler.submit(
        Observation(session_id="session", observation_id="second", created_at_ms=1_000)
    )

    for _ in range(10):
        if len(executor.started) == 2:
            break
        await asyncio.sleep(0)
    assert executor.started == ["first", "second"]
    assert not first.done()
    assert not second.done()

    executor.release.set()
    assert await first is not None
    assert await second is not None


@pytest.mark.asyncio
async def test_final_voice_sentences_share_one_turn_after_the_last_silence() -> None:
    room = _VoiceRoom()
    context = _VoiceContextBuilder()
    scheduler = _VoiceScheduler()
    ingest = IngestService(
        room_service=room,
        context_builder=context,
        frame_store=object(),
        asr_provider=object(),
        scheduler=scheduler,
        session_tasks=_SessionTasks(),
        clock=_VoiceClock(),
    )
    ingest._active_session_id = "session"

    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            text="第一句",
            started_at_ms=1_000,
            ended_at_ms=1_700,
            final=True,
        ),
    )
    await ingest._handle_transcript(
        "session",
        TranscriptSegment(
            session_id="session",
            text="第二句",
            started_at_ms=1_800,
            ended_at_ms=2_000,
            final=True,
        ),
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0.01)

    assert [event.text for event in room.events] == ["第一句", "第二句"]
    assert len(context.calls) == 1
    assert context.calls[0]["trigger_event_ids"] == ("voice-1", "voice-2")
    assert len(scheduler.observations) == 1


def _frame(index: int, *, change_score: float) -> FrameBundleItem:
    return FrameBundleItem(
        frame_id=f"frame-{index}",
        frame_index=index,
        captured_at_ms=index * 1_000,
        width=1280,
        height=720,
        encoding="jpeg",
        content_hash=f"{index:064x}",
        data_ref=f"frame:{index}",
        change_score=change_score,
    )


def test_smart_frame_timeline_keeps_anchors_and_caps_at_sixty() -> None:
    frames = tuple(
        _frame(index, change_score=0.7 if index in {15, 45, 75, 105} else 0.01)
        for index in range(120)
    )
    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=60, frame_window_ms=120_000),
        now_ms=119_000,
    )

    selected_times = {frame.captured_at_ms for frame in selected}
    assert len(selected) <= 60
    assert selected == tuple(sorted(selected, key=lambda frame: frame.captured_at_ms))
    assert {0, 5_000, 10_000, 15_000}.issubset(selected_times)
    assert {15_000, 45_000, 75_000, 105_000}.issubset(selected_times)


def _request() -> ViewerGenerationRequest:
    frame = _frame(1, change_score=0.5).model_copy(update={"frame_index": 0})
    assessment = SceneAssessment(
        assessment_id="assessment",
        room_id="room",
        session_id="session",
        audience_epoch=1,
        observation_id="observation",
        salience=1,
        novelty=1,
        emotional_intensity=0,
        replyable_event_ids=[],
        maximum_responses=1,
        created_at_ms=1,
        expires_at_ms=10_000,
    )
    persona = PersonaTemplate(
        persona_id="persona",
        document_version=1,
        revision=1,
        content_hash="1" * 64,
        display_name="观众",
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
    )
    return ViewerGenerationRequest(
        room_id="room",
        session_id="session",
        audience_epoch=1,
        observation_id="observation",
        generation_request_id="request",
        viewer_instance_id="viewer",
        viewer_sequence=1,
        username="viewer",
        display_name="观众",
        persona=persona,
        persona_revision=1,
        presence_revision=1,
        moderation_revision=1,
        behavior_revision=1,
        scene_assessment=assessment,
        instance_variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="game",
            silence_tendency=0.5,
        ),
        mode_context={},
        visual_input_mode=ViewerVisualInputMode.DIRECT_FRAMES,
        frame_bundle=FrameBundle(
            bundle_id="bundle",
            settings=FrameBundleSettings(frame_bundle_size=60),
            frames=[frame],
        ),
        viewer_private_state=ViewerPrivateState(),
        room_memory_slice=RoomMemorySlice(room_id="room", memory_revision=0),
        deadline_at_ms=10_000,
    )


def test_long_viewer_message_is_truncated_instead_of_discarded() -> None:
    request = _request()
    response = ViewerGenerationResponse(
        generation_request_id=request.generation_request_id,
        viewer_instance_id=request.viewer_instance_id,
        viewer_sequence=request.viewer_sequence,
        action=ViewerAction.BARRAGE,
        text="好" * 200,
        reaction_type="reaction",
        evidence_refs=[EvidenceRef(source=EvidenceSource.FRAME, frame_index=0)],
    )

    result = ViewerBarragePipeline(clock=_Clock(), id_generator=_Ids()).validate(
        request=request,
        response=response,
    )

    assert result.accepted
    assert result.event is not None
    assert result.event.text == "好" * 160
