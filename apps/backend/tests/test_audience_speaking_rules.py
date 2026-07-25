import asyncio
from types import SimpleNamespace

import pytest

from advx_backend.application.ingest_service import IngestService
from advx_backend.application.ports.asr import TranscriptSegment
from advx_backend.application.reaction_scheduler import (
    LatestWinsReactionScheduler,
    ReactionPreparationError,
    ReactionSchedulerConfig,
)
from advx_backend.application.reaction_service import ReactionResult
from advx_backend.application.viewer_barrage_pipeline import ViewerBarragePipeline
from advx_backend.application.viewer_runtime_coordinator import ViewerRuntimeCoordinator
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
    ViewerReactionTarget,
    ViewerTargetKind,
)
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation import Observation
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    FrameBundleSettings,
    ObservationTrigger,
    ObservationWave,
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


class _PreparationFailureExecutor:
    def __init__(self) -> None:
        self.attempts = 0

    async def react(self, observation: Observation) -> ReactionResult:
        del observation
        self.attempts += 1
        try:
            raise ValueError("frame metadata temporarily unavailable")
        except ValueError as error:
            raise ReactionPreparationError("preparation failed") from error


class _FlakyHistorySummarizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def summarize_history(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        existing_summary: str | None,
        older_history: str,
    ) -> str:
        del session_id, audience_epoch, existing_summary
        self.calls.append(older_history)
        if len(self.calls) == 1:
            raise RuntimeError("temporary history summary failure")
        return "已压缩的历史"


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
async def test_newer_equal_priority_observation_replaces_pending_work() -> None:
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

    assert await first_result is None
    assert await second_result is not None
    assert executor.observation_ids == ["second"]


@pytest.mark.asyncio
async def test_new_input_supersedes_work_that_has_not_started() -> None:
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
        if executor.started:
            break
        await asyncio.sleep(0)
    assert executor.started == ["second"]
    assert await first is None
    assert not second.done()

    executor.release.set()
    assert await second is not None


@pytest.mark.asyncio
async def test_preparation_failure_retries_once_and_is_reported() -> None:
    executor = _PreparationFailureExecutor()
    reported: list[tuple[str, str]] = []

    async def report(observation: Observation, error: Exception) -> None:
        reported.append((observation.observation_id, type(error.__cause__).__name__))

    scheduler = LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=_SessionTasks(),
        clock=_Clock(),
        config=ReactionSchedulerConfig(preparation_retry_backoff_ms=0),
        failure_reporter=report,
    )

    result = await scheduler.submit(
        Observation(session_id="session", observation_id="failed", created_at_ms=1_000)
    )

    assert await result is None
    assert executor.attempts == 2
    assert reported == [("failed", "ValueError")]


@pytest.mark.asyncio
async def test_failed_history_compaction_retries_older_events_on_next_wave() -> None:
    summarizer = _FlakyHistorySummarizer()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=object(),
        viewer_runtime=object(),
        history_summarizer=summarizer,
    )
    events = tuple(
        RoomEvent(
            event_id=f"event-{index}",
            session_id="session",
            sequence=index,
            source_type="user_text",
            source_id="host",
            created_at_ms=index,
            text=f"{index}:{'x' * 5_000}",
        )
        for index in range(1, 7)
    )
    observation = Observation(
        session_id="session",
        observation_id="observation",
        created_at_ms=1_000,
        room_events=events,
    )
    committed = SimpleNamespace(audience_epoch=1)

    first_recent, first_summary = await coordinator._compact_history(
        observation,
        committed,
    )
    state = coordinator._conversation_history["session"]

    assert first_summary is None
    assert len(first_recent) < len(events)
    assert state.covered_event_ids == set()

    second_recent, second_summary = await coordinator._compact_history(
        observation,
        committed,
    )

    assert summarizer.calls[0] == summarizer.calls[1]
    assert second_recent == first_recent
    assert second_summary == "已压缩的历史"
    assert state.covered_event_ids == {
        event.event_id for event in events if event not in first_recent
    }


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


@pytest.mark.asyncio
async def test_text_input_falls_back_to_text_only_when_no_frame_is_available() -> None:
    coordinator = ViewerRuntimeCoordinator(runtime_state=object(), viewer_runtime=object())
    wave = ObservationWave(
        room_id="room",
        session_id="session",
        audience_epoch=1,
        observation_id="observation",
        created_at_ms=1_000,
        deadline_at_ms=10_000,
        triggers=[ObservationTrigger.USER_TEXT],
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            viewer_visual_input_mode=ViewerVisualInputMode.DIRECT_FRAMES,
        )
    )

    prepared = await coordinator._prepare_visual_wave(wave, runtime)

    assert prepared is not None
    assert prepared.visual_input_mode is ViewerVisualInputMode.TEXT_ONLY
    assert prepared.frame_bundle is None


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
            settings=FrameBundleSettings(frame_bundle_size=15),
            frames=[frame],
        ),
        viewer_private_state=ViewerPrivateState(),
        room_memory_slice=RoomMemorySlice(room_id="room", memory_revision=0),
        deadline_at_ms=10_000,
    )


def test_viewer_reply_can_cite_the_bounded_reply_context() -> None:
    reply_event_id = "reply-event"
    base_request = _request()
    request = base_request.model_copy(
        update={
            "reply_context_event_ids": [reply_event_id],
            "scene_assessment": base_request.scene_assessment.model_copy(
                update={"replyable_event_ids": [reply_event_id]}
            ),
        }
    )
    response = ViewerGenerationResponse(
        generation_request_id=request.generation_request_id,
        viewer_instance_id=request.viewer_instance_id,
        viewer_sequence=request.viewer_sequence,
        action=ViewerAction.BARRAGE,
        target=ViewerReactionTarget(
            kind=ViewerTargetKind.EVENT,
            event_id=reply_event_id,
        ),
        text="我也觉得",
        reaction_type="reply",
        evidence_refs=[
            EvidenceRef(source=EvidenceSource.EVENT, event_id=reply_event_id)
        ],
    )

    result = ViewerBarragePipeline(clock=_Clock(), id_generator=_Ids()).validate(
        request=request,
        response=response,
    )

    assert result.accepted
    assert result.event is not None
    assert result.event.target == response.target
