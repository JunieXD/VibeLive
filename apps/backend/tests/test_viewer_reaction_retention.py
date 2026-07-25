import asyncio
from types import SimpleNamespace

import pytest

from advx_backend.application.reaction_scheduler import LatestWinsReactionScheduler
from advx_backend.application.reaction_service import ReactionResult
from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.viewer_barrage_pipeline import ViewerBarragePipeline
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.observation import Observation
from advx_backend.domain.observation_wave import (
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.room import RoomEvent, RoomEventSource
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstance, ViewerInstanceVariant


class _Clock:
    def now_ms(self) -> int:
        return 100


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"request-{self.value}"


class _SessionTasks:
    async def start_task(self, session_id, factory, *, name=None):
        del session_id
        return asyncio.create_task(factory(), name=name)

    async def accepts_results(self, session_id):
        del session_id
        return True


class _Sink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)

    async def append_published_barrage(self, event: object) -> None:
        self.events.append(event)


class _Fence:
    async def accepts(self, **scope: object) -> bool:
        del scope
        return True


class _ConcurrentProvider:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        if request.observation_id == "wave-1":
            self.first_started.set()
            await self.release_first.wait()
        else:
            self.second_started.set()
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            texts=[request.observation_id],
            reaction_type="reply",
            evidence_refs=[
                EvidenceRef(
                    source=EvidenceSource.EVENT,
                    event_id=f"event-{request.observation_id}",
                )
            ],
        )


def _observation(observation_id: str) -> Observation:
    event = RoomEvent(
        event_id=f"event-{observation_id}",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.USER_VOICE,
        created_at_ms=100,
        text=observation_id,
    )
    return Observation(
        session_id="session-1",
        observation_id=observation_id,
        created_at_ms=100,
        room_events=(event,),
        trigger_event_ids=(event.event_id,),
    )


def _wave(observation_id: str) -> ObservationWave:
    return ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=observation_id,
        created_at_ms=100,
        deadline_at_ms=10_000,
        triggers=[ObservationTrigger.FINAL_VOICE],
        event_ids=[f"event-{observation_id}"],
        trigger_event_ids=[f"event-{observation_id}"],
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="summary",
    )


def _decision(observation_id: str) -> CrowdDecision:
    return CrowdDecision(
        decision_id=f"decision-{observation_id}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=observation_id,
        selected_viewer_ids=["viewer-1"],
        evidence_event_ids=[f"event-{observation_id}"],
        created_at_ms=100,
        expires_at_ms=10_000,
    )


def _viewer() -> ViewerInstance:
    return ViewerInstance(
        viewer_instance_id="viewer-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        persona_id="persona-1",
        persona_revision=1,
        ordinal=1,
        username="viewer",
        display_name="Viewer",
        avatar_seed="avatar",
        color_seed="color",
        variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="gameplay",
            silence_tendency=0.2,
        ),
        created_at_ms=0,
        joined_at_ms=0,
        join_count=1,
    )


def _runtime_context() -> object:
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash="1" * 64,
        display_name="Viewer",
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
    )
    return SimpleNamespace(
        settings=SimpleNamespace(allow_viewer_silence=True),
        canonical_runtime_spec=SimpleNamespace(
            config_revision=1,
            active_mode_id=None,
            modes=(),
            personas=(persona,),
            settings=SimpleNamespace(
                max_in_flight_viewer_requests=2,
                viewer_queue_capacity=8,
                viewer_request_start_interval_ms=0,
                viewer_request_ttl_ms=9_900,
            ),
        ),
        scene_assessment=SceneAssessment(
            assessment_id="assessment",
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            observation_id="wave-1",
            salience=1,
            novelty=1,
            emotional_intensity=0,
            replyable_event_ids=[],
            maximum_responses=1,
            created_at_ms=100,
            expires_at_ms=10_000,
        ),
    )


async def _runtime_state_store() -> RuntimeStateStore:
    store = RuntimeStateStore()
    await store.activate(
        CommittedRuntime(
            session_id="session-1",
            spec=SimpleNamespace(room=SimpleNamespace(room_id="room-1")),
            audience_epoch=1,
            pool=SimpleNamespace(viewers=(_viewer(),)),
        )
    )
    return store


@pytest.mark.asyncio
async def test_user_observations_run_concurrently_without_supersession() -> None:
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[str] = set()

    class Executor:
        async def react(self, observation: Observation) -> ReactionResult:
            started.add(observation.observation_id)
            if len(started) == 2:
                both_started.set()
            await release.wait()
            return ReactionResult(published_events=(), validations=())

    scheduler = LatestWinsReactionScheduler(
        executor=Executor(),
        session_tasks=_SessionTasks(),
        clock=_Clock(),
    )

    first = await scheduler.submit(_observation("first"))
    second = await scheduler.submit(_observation("second"))
    await asyncio.wait_for(both_started.wait(), timeout=0.2)

    release.set()
    assert await first is not None
    assert await second is not None
    assert started == {"first", "second"}


@pytest.mark.asyncio
async def test_new_wave_does_not_block_or_discard_late_old_output() -> None:
    provider = _ConcurrentProvider()
    publisher = _Sink()
    room = _Sink()
    clock = _Clock()
    state_store = await _runtime_state_store()
    runtime = ViewerRuntime(
        provider=provider,
        barrage_pipeline=ViewerBarragePipeline(clock=clock, id_generator=_Ids()),
        session_fence=state_store,
        publisher=publisher,
        room_service=room,
        clock=clock,
        id_generator=_Ids(),
        max_in_flight=2,
    )
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer(),))
    context = _runtime_context()

    first = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-1"),
            decision=_decision("wave-1"),
            pool=pool,
            runtime=context,
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=0.2)
    second = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-2"),
            decision=_decision("wave-2"),
            pool=pool,
            runtime=context,
        )
    )
    await asyncio.wait_for(provider.second_started.wait(), timeout=0.2)
    second_summary = await asyncio.wait_for(second, timeout=0.5)

    provider.release_first.set()
    first_summary = await asyncio.wait_for(first, timeout=1)

    assert second_summary.published == 1
    assert first_summary.published == 1
    assert first_summary.stale == 0
    assert first_summary.cancelled == 0
    assert [event.text for event in publisher.events] == ["wave-2", "wave-1"]
    assert [event.text for event in room.events] == ["wave-2", "wave-1"]


@pytest.mark.asyncio
async def test_runtime_state_accepts_all_issued_sequences_until_session_stop() -> None:
    state_store = await _runtime_state_store()

    for sequence in (1, 2):
        assert await state_store.claim_viewer_sequence(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            viewer_instance_id="viewer-1",
            viewer_sequence=sequence,
        )

    for sequence in (1, 2):
        assert await state_store.accepts(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            viewer_instance_id="viewer-1",
            viewer_sequence=sequence,
        )
    assert not await state_store.accepts(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        viewer_instance_id="viewer-1",
        viewer_sequence=3,
    )

    await state_store.stop_session("session-1")
    assert not await state_store.accepts(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        viewer_instance_id="viewer-1",
        viewer_sequence=1,
    )


@pytest.mark.asyncio
async def test_session_stop_still_cancels_unfinished_output() -> None:
    provider = _ConcurrentProvider()
    publisher = _Sink()
    room = _Sink()
    clock = _Clock()
    runtime = ViewerRuntime(
        provider=provider,
        barrage_pipeline=ViewerBarragePipeline(clock=clock, id_generator=_Ids()),
        session_fence=_Fence(),
        publisher=publisher,
        room_service=room,
        clock=clock,
        id_generator=_Ids(),
        max_in_flight=2,
    )
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-1"),
            decision=_decision("wave-1"),
            pool=SimpleNamespace(viewers=(_viewer(),)),
            runtime=_runtime_context(),
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=0.2)

    await asyncio.wait_for(runtime.stop_session("session-1"), timeout=0.5)
    summary = await asyncio.wait_for(dispatch, timeout=0.5)

    assert summary.cancelled == 1
    assert publisher.events == []
    assert room.events == []
