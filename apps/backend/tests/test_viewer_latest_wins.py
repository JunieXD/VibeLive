import asyncio
from types import SimpleNamespace

import pytest

from advx_backend.application.reaction_scheduler import (
    LatestWinsReactionScheduler,
    ReactionSchedulerConfig,
)
from advx_backend.application.reaction_service import ReactionResult
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.observation import FrameRef, Observation
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
    def __init__(self, value: int = 100) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value


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


def _observation(
    observation_id: str,
    *,
    source: RoomEventSource | None = None,
    ambient: bool = False,
) -> Observation:
    event = (
        RoomEvent(
            event_id=f"event-{observation_id}",
            session_id="session-1",
            sequence=1,
            source_type=source,
            created_at_ms=100,
            text="input",
        )
        if source is not None
        else None
    )
    frame = FrameRef(
        frame_id=f"frame-{observation_id}",
        created_at_ms=100,
        mime_type="image/jpeg",
        data_ref=f"frame://{observation_id}",
    )
    return Observation(
        session_id="session-1",
        observation_id=observation_id,
        created_at_ms=100,
        frames=(frame,),
        room_events=(() if event is None else (event,)),
        trigger_event_ids=(() if event is None else (event.event_id,)),
        trigger_frame_ids=(frame.frame_id,),
        user_context={"ambient": "true"} if ambient else {},
    )


def test_system_audio_priority_is_between_user_and_ambient() -> None:
    system_event = RoomEvent(
        event_id="event-system",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.SYSTEM_EVENT,
        created_at_ms=100,
        text="video dialogue",
        payload={"event": "system_audio_transcript"},
    )
    system_audio = Observation(
        session_id="session-1",
        observation_id="system",
        created_at_ms=100,
        room_events=(system_event,),
        trigger_event_ids=(system_event.event_id,),
    )
    ambient = Observation(
        session_id="session-1",
        observation_id="ambient",
        created_at_ms=100,
        user_context={"ambient": "true"},
    )
    user = _observation("user-priority", source=RoomEventSource.USER_TEXT)

    assert LatestWinsReactionScheduler._priority(user) == 3
    assert LatestWinsReactionScheduler._priority(system_audio) == 2
    assert LatestWinsReactionScheduler._priority(ambient) == 1


@pytest.mark.asyncio
async def test_scheduler_honors_configured_one_second_merge(monkeypatch) -> None:
    real_sleep = asyncio.sleep

    async def immediate_sleep(delay: float) -> None:
        assert delay == 1
        await real_sleep(0)

    monkeypatch.setattr(
        "advx_backend.application.reaction_scheduler.asyncio.sleep",
        immediate_sleep,
    )

    class Executor:
        observations: list[Observation] = []

        async def react(self, observation: Observation) -> ReactionResult:
            self.observations.append(observation)
            return ReactionResult(published_events=(), validations=())

    executor = Executor()
    scheduler = LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=_SessionTasks(),
        clock=_Clock(),
        config=ReactionSchedulerConfig(observation_merge_window_ms=1_000),
    )

    first = await scheduler.submit(
        _observation("screen", source=RoomEventSource.SCREEN_OBSERVATION)
    )
    latest = await scheduler.submit(
        _observation("user", source=RoomEventSource.USER_TEXT)
    )

    assert await first is not None
    assert await latest is not None
    assert [item.observation_id for item in executor.observations] == ["user"]
    assert len(executor.observations[0].frames) == 2


@pytest.mark.asyncio
async def test_scheduler_lower_priority_does_not_interrupt_user_work() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Executor:
        observations: list[str] = []

        async def react(self, observation: Observation) -> ReactionResult:
            self.observations.append(observation.observation_id)
            started.set()
            await release.wait()
            return ReactionResult(published_events=(), validations=())

    executor = Executor()
    scheduler = LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=_SessionTasks(),
        clock=_Clock(),
    )
    user = await scheduler.submit(_observation("user", source=RoomEventSource.USER_TEXT))
    await started.wait()
    screen = await scheduler.submit(
        _observation("screen", source=RoomEventSource.SCREEN_OBSERVATION)
    )

    assert await screen is None
    release.set()
    assert await user is not None
    assert executor.observations == ["user"]


@pytest.mark.asyncio
async def test_ambient_history_does_not_inherit_old_user_priority() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Executor:
        observations: list[str] = []

        async def react(self, observation: Observation) -> ReactionResult:
            self.observations.append(observation.observation_id)
            started.set()
            await release.wait()
            return ReactionResult(published_events=(), validations=())

    scheduler = LatestWinsReactionScheduler(
        executor=Executor(),
        session_tasks=_SessionTasks(),
        clock=_Clock(),
    )
    user = await scheduler.submit(_observation("user", source=RoomEventSource.USER_TEXT))
    await started.wait()
    historical_user = RoomEvent(
        event_id="historical-user",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.USER_TEXT,
        created_at_ms=50,
        text="old context",
    )
    ambient_observation = Observation(
        session_id="session-1",
        observation_id="ambient",
        created_at_ms=100,
        room_events=(historical_user,),
        user_context={"ambient": "true"},
    )

    ambient = await scheduler.submit(ambient_observation)

    assert await ambient is None
    release.set()
    assert await user is not None


@pytest.mark.asyncio
async def test_merge_window_keeps_only_bounded_completion_waiters(monkeypatch) -> None:
    real_sleep = asyncio.sleep
    release_merge = asyncio.Event()

    async def controlled_sleep(delay: float) -> None:
        assert delay == 1
        await release_merge.wait()
        await real_sleep(0)

    monkeypatch.setattr(
        "advx_backend.application.reaction_scheduler.asyncio.sleep",
        controlled_sleep,
    )

    class Executor:
        observations: list[str] = []

        async def react(self, observation: Observation) -> ReactionResult:
            self.observations.append(observation.observation_id)
            return ReactionResult(published_events=(), validations=())

    executor = Executor()
    scheduler = LatestWinsReactionScheduler(
        executor=executor,
        session_tasks=_SessionTasks(),
        clock=_Clock(),
        config=ReactionSchedulerConfig(
            observation_merge_window_ms=1_000,
            max_pending_observations_per_session=2,
        ),
    )
    first = await scheduler.submit(_observation("first", source=RoomEventSource.USER_TEXT))
    await real_sleep(0)
    second = await scheduler.submit(_observation("second", source=RoomEventSource.USER_TEXT))
    latest = await scheduler.submit(_observation("latest", source=RoomEventSource.USER_TEXT))

    assert await first is None
    release_merge.set()
    assert await second is not None
    assert await latest is not None
    assert executor.observations == ["latest"]


def _viewer(viewer_id: str = "viewer-1") -> ViewerInstance:
    return ViewerInstance(
        viewer_instance_id=viewer_id,
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


def _wave(
    observation_id: str,
    trigger: ObservationTrigger = ObservationTrigger.USER_TEXT,
) -> ObservationWave:
    return ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=observation_id,
        created_at_ms=100,
        deadline_at_ms=10_000,
        triggers=[trigger],
        event_ids=[f"event-{observation_id}"],
        trigger_event_ids=[f"event-{observation_id}"],
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="summary",
    )


def _decision(observation_id: str, *viewer_ids: str) -> CrowdDecision:
    return CrowdDecision(
        decision_id=f"decision-{observation_id}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=observation_id,
        selected_viewer_ids=list(viewer_ids),
        evidence_event_ids=[f"event-{observation_id}"],
        created_at_ms=100,
        expires_at_ms=10_000,
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
        canonical_runtime_spec=SimpleNamespace(
            config_revision=1,
            active_mode_id=None,
            modes=(),
            personas=(persona,),
            settings=SimpleNamespace(
                max_in_flight_viewer_requests=1,
                viewer_queue_capacity=1,
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


class _Fence:
    async def accepts(self, **scope: object) -> bool:
        del scope
        return True


class _Pipeline:
    def validate(self, *, request: object, response: object) -> object:
        del request
        return SimpleNamespace(accepted=True, event=response, rejection_reason=None)


class _Sink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)

    async def append_published_barrage(self, event: object) -> None:
        self.events.append(event)


class _GatedProvider:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.started = asyncio.Event()
        self.release_old = asyncio.Event()

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        if request.observation_id == "wave-1":
            self.started.set()
            try:
                await self.release_old.wait()
            except asyncio.CancelledError:
                await self.release_old.wait()
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            text=request.observation_id,
            reaction_type="reply",
            evidence_refs=[
                EvidenceRef(
                    source=EvidenceSource.EVENT,
                    event_id=f"event-{request.observation_id}",
                )
            ],
        )


def _runtime(provider: object) -> tuple[ViewerRuntime, _Sink, _Sink]:
    publisher = _Sink()
    room = _Sink()
    runtime = ViewerRuntime(
        provider=provider,
        barrage_pipeline=_Pipeline(),
        session_fence=_Fence(),
        publisher=publisher,
        room_service=room,
        clock=_Clock(),
        id_generator=_Ids(),
        max_in_flight=1,
    )
    return runtime, publisher, room


@pytest.mark.asyncio
async def test_system_audio_wave_calls_viewer_provider_and_publishes() -> None:
    provider = _GatedProvider()
    runtime, publisher, room = _runtime(provider)
    await runtime.start_session("session-1")

    summary = await runtime.dispatch(
        wave=_wave("system-wave", ObservationTrigger.SYSTEM_AUDIO),
        decision=_decision("system-wave", "viewer-1"),
        pool=SimpleNamespace(viewers=(_viewer(),)),
        runtime=_runtime_context(),
    )

    assert summary.published == 1
    assert [request.observation_id for request in provider.requests] == [
        "system-wave"
    ]
    assert len(publisher.events) == 1
    assert len(room.events) == 1


@pytest.mark.asyncio
async def test_newer_unselected_wave_fences_old_inflight_result_with_zero_side_effects() -> None:
    provider = _GatedProvider()
    runtime, publisher, room = _runtime(provider)
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer(),))
    first = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-1"),
            decision=_decision("wave-1", "viewer-1"),
            pool=pool,
            runtime=_runtime_context(),
        )
    )
    await provider.started.wait()

    newer = await runtime.dispatch(
        wave=_wave("wave-2"),
        decision=_decision("wave-2"),
        pool=pool,
        runtime=_runtime_context(),
    )
    provider.release_old.set()
    old = await first

    assert newer.selected == 0
    assert old.superseded == 1
    assert publisher.events == []
    assert room.events == []


@pytest.mark.asyncio
async def test_mailbox_keeps_only_latest_pending_equal_priority_item() -> None:
    provider = _GatedProvider()
    runtime, _, _ = _runtime(provider)
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer(),))
    context = _runtime_context()
    first = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-1"),
            decision=_decision("wave-1", "viewer-1"),
            pool=pool,
            runtime=context,
        )
    )
    await provider.started.wait()
    second = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-2"),
            decision=_decision("wave-2", "viewer-1"),
            pool=pool,
            runtime=context,
        )
    )
    await asyncio.sleep(0)
    latest = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-3"),
            decision=_decision("wave-3", "viewer-1"),
            pool=pool,
            runtime=context,
        )
    )
    await asyncio.sleep(0)

    mailbox = runtime._mailboxes["viewer-1"]
    assert mailbox.pending is not None
    assert mailbox.pending.request.observation_id == "wave-3"
    provider.release_old.set()
    first_summary, second_summary, latest_summary = await asyncio.gather(
        first,
        second,
        latest,
    )

    assert first_summary.superseded == 1
    assert second_summary.superseded == 1
    assert latest_summary.published == 1
    assert [request.observation_id for request in provider.requests] == [
        "wave-1",
        "wave-3",
    ]


@pytest.mark.asyncio
async def test_lower_priority_wave_does_not_interrupt_inflight_user_wave() -> None:
    provider = _GatedProvider()
    runtime, publisher, room = _runtime(provider)
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer(),))
    user = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("wave-1"),
            decision=_decision("wave-1", "viewer-1"),
            pool=pool,
            runtime=_runtime_context(),
        )
    )
    await provider.started.wait()

    ambient = await runtime.dispatch(
        wave=_wave("ambient", ObservationTrigger.AMBIENT_TICK),
        decision=_decision("ambient", "viewer-1"),
        pool=pool,
        runtime=_runtime_context(),
    )
    provider.release_old.set()
    user_summary = await user

    assert ambient.superseded == 1
    assert user_summary.published == 1
    assert len(publisher.events) == 1
    assert len(room.events) == 1

    later_ambient = await runtime.dispatch(
        wave=_wave("ambient-later", ObservationTrigger.AMBIENT_TICK),
        decision=_decision("ambient-later", "viewer-1"),
        pool=pool,
        runtime=_runtime_context(),
    )
    assert later_ambient.published == 1
