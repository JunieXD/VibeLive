import asyncio
from types import SimpleNamespace

import pytest

from advx_backend.application.reaction_scheduler import (
    LatestWinsReactionScheduler,
    ReactionSchedulerConfig,
)
from advx_backend.application.reaction_service import ReactionResult
from advx_backend.application.viewer_barrage_pipeline import ViewerBarragePipeline
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.contracts.debug import TraceResponseStatus
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationResponse,
    WindowBatchGenerationResponse,
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
        trigger_frame_ids=(
            (frame.frame_id,) if source is RoomEventSource.SCREEN_OBSERVATION else ()
        ),
        user_context={"ambient": "true"} if ambient else {},
    )


def _system_audio_observation(observation_id: str) -> Observation:
    event = RoomEvent(
        event_id=f"event-{observation_id}",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.SYSTEM_EVENT,
        created_at_ms=100,
        text="video dialogue",
        payload={"event": "system_audio_transcript"},
    )
    return Observation(
        session_id="session-1",
        observation_id=observation_id,
        created_at_ms=100,
        room_events=(event,),
        trigger_event_ids=(event.event_id,),
    )


def test_system_audio_priority_is_between_user_and_ambient() -> None:
    system_audio = _system_audio_observation("system")
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
async def test_user_input_replaces_a_pending_screen_trigger(monkeypatch) -> None:
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

    assert await first is None
    assert await latest is not None
    assert [item.observation_id for item in executor.observations] == ["user"]
    assert len(executor.observations[0].frames) == 1


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
async def test_screen_trigger_does_not_interrupt_ambient_work() -> None:
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
    ambient = await scheduler.submit(_observation("ambient", ambient=True))
    await started.wait()
    screen = await scheduler.submit(
        _observation("screen", source=RoomEventSource.SCREEN_OBSERVATION)
    )

    assert await screen is None
    release.set()
    assert await ambient is not None
    assert executor.observations == ["ambient"]


@pytest.mark.asyncio
async def test_screen_trigger_does_not_interrupt_system_audio_work() -> None:
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
    system_audio = await scheduler.submit(_system_audio_observation("system"))
    await started.wait()
    screen = await scheduler.submit(
        _observation("screen", source=RoomEventSource.SCREEN_OBSERVATION)
    )

    assert await screen is None
    release.set()
    assert await system_audio is not None
    assert executor.observations == ["system"]


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


class _TaskOwnedFence:
    """Models the runtime effect boundary's task-owned reentrancy."""

    def __init__(self) -> None:
        self._owner = None
        self.release_cross_task_update = asyncio.Event()
        self.owner_updates = 0
        self.cross_task_updates = 0

    async def accepts(self, **scope: object) -> bool:
        del scope
        return True

    async def execute_if_accepting(self, *, operation, **scope: object):
        del scope
        task = asyncio.current_task()
        assert task is not None
        if self._owner is task:
            return True, await operation()
        assert self._owner is None
        self._owner = task
        try:
            return True, await operation()
        finally:
            self._owner = None

    async def record_behavior_update(self) -> None:
        if asyncio.current_task() is self._owner:
            self.owner_updates += 1
            return
        self.cross_task_updates += 1
        await self.release_cross_task_update.wait()


class _Sink:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)

    async def append_published_barrage(self, event: object) -> None:
        self.events.append(event)


class _TraceSink:
    def __init__(self) -> None:
        self.traces: list[object] = []

    def record(self, trace: object) -> None:
        self.traces.append(trace)


class _FenceBehaviorSink:
    def __init__(self, fence: _TaskOwnedFence) -> None:
        self._fence = fence
        self.published_observation_ids: list[str] = []

    async def record_published(self, request: object, event: object) -> None:
        del event
        await self._fence.record_behavior_update()
        self.published_observation_ids.append(request.observation_id)

    async def record_silence(self, request: object) -> None:
        del request


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
            texts=[request.observation_id],
            reaction_type="reply",
            evidence_refs=[
                EvidenceRef(
                    source=EvidenceSource.EVENT,
                    event_id=f"event-{request.observation_id}",
                )
            ],
        )


class _BatchProvider:
    def __init__(
        self,
        *,
        response_batch_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.requests: list[object] = []
        self.response_batch_id = response_batch_id
        self.error = error

    async def generate_window_batch(self, request: object) -> WindowBatchGenerationResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        candidates = [
            ViewerGenerationResponse(
                generation_request_id=item.generation_request_id,
                viewer_instance_id=item.viewer_instance_id,
                viewer_sequence=item.viewer_sequence,
                action=ViewerAction.BARRAGE,
                texts=["same text" if index < 2 else f"text-{index}"],
                reaction_type="reply",
                evidence_refs=[
                    EvidenceRef(
                        source=EvidenceSource.EVENT,
                        event_id="event-window",
                    )
                ],
            )
            for index, item in enumerate(request.requests)
        ]
        return WindowBatchGenerationResponse(
            batch_generation_request_id=(
                self.response_batch_id or request.batch_generation_request_id
            ),
            candidates=candidates,
        )


class _LaneBatchProvider(_BatchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = [asyncio.Event(), asyncio.Event()]
        self.release = [asyncio.Event(), asyncio.Event()]

    async def generate_window_batch(self, request: object) -> WindowBatchGenerationResponse:
        index = len(self.requests)
        self.requests.append(request)
        if index < len(self.started):
            self.started[index].set()
            await self.release[index].wait()
        return WindowBatchGenerationResponse(
            batch_generation_request_id=request.batch_generation_request_id,
            candidates=[
                ViewerGenerationResponse(
                    generation_request_id=item.generation_request_id,
                    viewer_instance_id=item.viewer_instance_id,
                    viewer_sequence=item.viewer_sequence,
                    action=ViewerAction.BARRAGE,
                    texts=[f"batch-{index}-{item.viewer_instance_id}"],
                    reaction_type="reply",
                    evidence_refs=[
                        EvidenceRef(
                            source=EvidenceSource.EVENT,
                            event_id="event-window",
                        )
                    ],
                )
                for item in request.requests
            ],
        )


class _CancellationResistantBatchProvider(_BatchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()
        self.release_first = asyncio.Event()

    async def generate_window_batch(self, request: object) -> WindowBatchGenerationResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_started.set()
            try:
                await self.release_first.wait()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                await self.release_first.wait()
        return WindowBatchGenerationResponse(
            batch_generation_request_id=request.batch_generation_request_id,
            candidates=[
                ViewerGenerationResponse(
                    generation_request_id=item.generation_request_id,
                    viewer_instance_id=item.viewer_instance_id,
                    viewer_sequence=item.viewer_sequence,
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
                for item in request.requests
            ],
        )


class _GatedSequenceClaimer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.second_started = asyncio.Event()

    async def claim_viewer_sequence(self, **scope: object) -> bool:
        self.calls.append(str(scope["viewer_instance_id"]))
        if len(self.calls) == 2:
            self.second_started.set()
            await asyncio.Event().wait()
        return True


class _RejectThenGateSequenceClaimer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.second_started = asyncio.Event()

    async def claim_viewer_sequence(self, **scope: object) -> bool:
        self.calls.append(str(scope["viewer_instance_id"]))
        if len(self.calls) == 1:
            return False
        self.second_started.set()
        await asyncio.Event().wait()
        return True


class _BurstProvider:
    async def generate(self, request: object) -> ViewerGenerationResponse:
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            texts=["第一条", "第二条", "第三条"],
            reaction_type="comment",
        )


class _WindowBurstProvider:
    async def generate_window_batch(self, request: object) -> WindowBatchGenerationResponse:
        return WindowBatchGenerationResponse(
            batch_generation_request_id=request.batch_generation_request_id,
            candidates=[
                ViewerGenerationResponse(
                    generation_request_id=item.generation_request_id,
                    viewer_instance_id=item.viewer_instance_id,
                    viewer_sequence=item.viewer_sequence,
                    action=ViewerAction.BARRAGE,
                    texts=["第一条", "第二条"],
                    reaction_type="comment",
                )
                for item in request.requests
            ],
        )


class _BatchBehaviorSink:
    def __init__(self) -> None:
        self.batches: list[tuple[object, ...]] = []

    async def record_published(self, request: object, event: object) -> None:
        del request
        assert isinstance(event, tuple)
        self.batches.append(event)

    async def record_silence(self, request: object) -> None:
        del request


def _runtime(
    provider: object,
    *,
    fence: object | None = None,
    behavior_state_sink: object | None = None,
    trace_recorder: object | None = None,
    sequence_claimer: object | None = None,
    barrage_pipeline: object | None = None,
    clock: object | None = None,
    sleeper: object | None = None,
) -> tuple[ViewerRuntime, _Sink, _Sink]:
    publisher = _Sink()
    room = _Sink()
    runtime_clock = _Clock() if clock is None else clock
    ids = _Ids()
    runtime_args = {} if sleeper is None else {"sleeper": sleeper}
    runtime = ViewerRuntime(
        provider=provider,
        barrage_pipeline=(
            ViewerBarragePipeline(clock=runtime_clock, id_generator=ids)
            if barrage_pipeline is None
            else barrage_pipeline
        ),
        session_fence=_Fence() if fence is None else fence,
        publisher=publisher,
        room_service=room,
        clock=runtime_clock,
        id_generator=ids,
        max_in_flight=1,
        behavior_state_sink=behavior_state_sink,
        trace_recorder=trace_recorder,
        sequence_claimer=sequence_claimer,
        **runtime_args,
    )
    return runtime, publisher, room


@pytest.mark.asyncio
async def test_barrage_batch_publishes_each_text_after_the_configured_interval() -> None:
    clock = _Clock()
    sleeps: list[float] = []

    async def advance_clock(delay: float) -> None:
        sleeps.append(delay)
        clock.value += int(delay * 1_000)

    behavior = _BatchBehaviorSink()
    runtime, publisher, room = _runtime(
        _BurstProvider(),
        barrage_pipeline=ViewerBarragePipeline(clock=clock, id_generator=_Ids()),
        clock=clock,
        sleeper=advance_clock,
        behavior_state_sink=behavior,
    )
    await runtime.start_session("session-1")

    summary = await runtime.dispatch(
        wave=_wave("batch-wave"),
        decision=_decision("batch-wave", "viewer-1"),
        pool=SimpleNamespace(viewers=(_viewer(),)),
        runtime=_runtime_context(),
    )

    assert summary.published == 1
    assert sleeps == [0.2, 0.2]
    assert [event.text for event in publisher.events] == ["第一条", "第二条", "第三条"]
    assert [event.text for event in room.events] == ["第一条", "第二条", "第三条"]
    assert [event.created_at_ms for event in publisher.events] == [100, 300, 500]
    assert len(behavior.batches) == 1
    assert [event.text for event in behavior.batches[0]] == ["第一条", "第二条", "第三条"]


@pytest.mark.asyncio
async def test_window_batch_candidate_publishes_a_barrage_burst() -> None:
    clock = _Clock()
    sleeps: list[float] = []

    async def advance_clock(delay: float) -> None:
        sleeps.append(delay)
        clock.value += int(delay * 1_000)

    runtime, publisher, room = _runtime(
        _WindowBurstProvider(),
        barrage_pipeline=ViewerBarragePipeline(clock=clock, id_generator=_Ids()),
        clock=clock,
        sleeper=advance_clock,
    )
    await runtime.start_session("session-1")

    summary = await runtime.dispatch_window_batch(
        wave=_wave("window-burst"),
        decision=_decision("window-burst", "viewer-1"),
        pool=SimpleNamespace(viewers=(_viewer(),)),
        runtime=_runtime_context(),
    )

    assert summary.published == 1
    assert sleeps == [0.2]
    assert [event.text for event in publisher.events] == ["第一条", "第二条"]
    assert [event.text for event in room.events] == ["第一条", "第二条"]
    assert [event.created_at_ms for event in publisher.events] == [100, 300]


@pytest.mark.asyncio
async def test_superseding_wave_drops_unpublished_barrage_batch_remainder() -> None:
    clock = _Clock()
    sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()

    async def blocked_sleep(delay: float) -> None:
        assert delay == 0.2
        sleep_started.set()
        await release_sleep.wait()

    runtime, publisher, room = _runtime(
        _BurstProvider(),
        barrage_pipeline=ViewerBarragePipeline(clock=clock, id_generator=_Ids()),
        clock=clock,
        sleeper=blocked_sleep,
    )
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer(),))
    dispatch = asyncio.create_task(
        runtime.dispatch(
            wave=_wave("batch-wave"),
            decision=_decision("batch-wave", "viewer-1"),
            pool=pool,
            runtime=_runtime_context(),
        )
    )
    await sleep_started.wait()

    newer = await runtime.dispatch(
        wave=_wave("newer-wave"),
        decision=_decision("newer-wave"),
        pool=pool,
        runtime=_runtime_context(),
    )
    release_sleep.set()
    summary = await dispatch

    assert newer.selected == 0
    assert summary.published == 1
    assert [event.text for event in publisher.events] == ["第一条"]
    assert [event.text for event in room.events] == ["第一条"]


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
async def test_published_screen_waves_keep_behavior_update_in_fence_task() -> None:
    provider = _GatedProvider()
    fence = _TaskOwnedFence()
    behavior = _FenceBehaviorSink(fence)
    runtime, publisher, room = _runtime(
        provider,
        fence=fence,
        behavior_state_sink=behavior,
    )
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer(),))
    context = _runtime_context()

    async def dispatch_screen_wave(observation_id: str):
        dispatch = asyncio.create_task(
            runtime.dispatch(
                wave=_wave(observation_id, ObservationTrigger.SCREEN_CHANGE),
                decision=_decision(observation_id, "viewer-1"),
                pool=pool,
                runtime=context,
            )
        )
        done, _ = await asyncio.wait({dispatch}, timeout=1)
        if dispatch not in done:
            fence.release_cross_task_update.set()
            await asyncio.gather(dispatch, return_exceptions=True)
            pytest.fail("published viewer behavior update waited on a different fence task")
        return dispatch.result()

    first = await dispatch_screen_wave("screen-1")
    second = await dispatch_screen_wave("screen-2")

    assert first.published == 1
    assert second.published == 1
    assert fence.owner_updates == 2
    assert fence.cross_task_updates == 0
    assert behavior.published_observation_ids == ["screen-1", "screen-2"]
    assert len(publisher.events) == 2
    assert len(room.events) == 2


@pytest.mark.asyncio
async def test_window_batch_calls_provider_once_and_reuses_trusted_publish_pipeline() -> None:
    provider = _BatchProvider()
    publisher = _Sink()
    room = _Sink()
    runtime = ViewerRuntime(
        provider=provider,
        barrage_pipeline=ViewerBarragePipeline(clock=_Clock(), id_generator=_Ids()),
        session_fence=_Fence(),
        publisher=publisher,
        room_service=room,
        clock=_Clock(),
        id_generator=_Ids(),
        max_in_flight=1,
    )
    await runtime.start_session("session-1")

    summary = await runtime.dispatch_window_batch(
        wave=_wave("window"),
        decision=_decision("window", "viewer-1", "viewer-2"),
        pool=SimpleNamespace(viewers=(_viewer("viewer-1"), _viewer("viewer-2"))),
        runtime=_runtime_context(),
    )

    assert len(provider.requests) == 1
    assert [item.viewer_instance_id for item in provider.requests[0].requests] == [
        "viewer-1",
        "viewer-2",
    ]
    assert summary.published == 1
    assert summary.silenced == 1
    assert len(publisher.events) == 1
    assert len(room.events) == 1


@pytest.mark.asyncio
async def test_window_batch_rejects_mismatched_batch_response_id() -> None:
    provider = _BatchProvider(response_batch_id="wrong-batch")
    runtime, publisher, room = _runtime(provider)
    await runtime.start_session("session-1")

    summary = await runtime.dispatch_window_batch(
        wave=_wave("window"),
        decision=_decision("window", "viewer-1", "viewer-2"),
        pool=SimpleNamespace(viewers=(_viewer("viewer-1"), _viewer("viewer-2"))),
        runtime=_runtime_context(),
    )

    assert summary.failed == 2
    assert summary.completed == 2
    assert not publisher.events
    assert not room.events


@pytest.mark.asyncio
async def test_window_batch_timeout_is_a_completed_terminal_outcome() -> None:
    provider = _BatchProvider(error=TimeoutError())
    runtime, publisher, room = _runtime(provider)
    await runtime.start_session("session-1")

    summary = await runtime.dispatch_window_batch(
        wave=_wave("window"),
        decision=_decision("window", "viewer-1", "viewer-2"),
        pool=SimpleNamespace(viewers=(_viewer("viewer-1"), _viewer("viewer-2"))),
        runtime=_runtime_context(),
    )

    assert summary.dispatched == 2
    assert summary.completed == 2
    assert summary.expired == 2
    assert not publisher.events
    assert not room.events


@pytest.mark.asyncio
async def test_window_batch_reuses_runtime_lane_and_enforces_queue_capacity() -> None:
    provider = _LaneBatchProvider()
    traces = _TraceSink()
    runtime, _, _ = _runtime(provider, trace_recorder=traces)
    await runtime.start_session("session-1")
    wave = _wave("window")
    decision = _decision("window", "viewer-1", "viewer-2")
    pool = SimpleNamespace(viewers=(_viewer("viewer-1"), _viewer("viewer-2")))

    first = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=wave,
            decision=decision,
            pool=pool,
            runtime=_runtime_context(),
        )
    )
    await provider.started[0].wait()
    second = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=wave,
            decision=decision,
            pool=pool,
            runtime=_runtime_context(),
        )
    )
    await asyncio.sleep(0)

    third = await runtime.dispatch_window_batch(
        wave=wave,
        decision=decision,
        pool=pool,
        runtime=_runtime_context(),
    )

    assert len(provider.requests) == 1
    assert third.cancelled == 2
    rejected = [
        trace
        for trace in traces.traces
        if getattr(trace, "observation_id", None) == "window"
        and getattr(trace, "stale_or_cancel_reason", None)
        == "window_batch_queue_capacity_exceeded"
    ]
    assert len(rejected) == 2
    assert all(trace.response_status is TraceResponseStatus.CANCELLED for trace in rejected)
    assert all(trace.validation.codes == ["queue_capacity_exceeded"] for trace in rejected)
    provider.release[0].set()
    await provider.started[1].wait()
    provider.release[1].set()
    first_summary, second_summary = await asyncio.gather(first, second)

    assert first_summary.published == 2
    assert second_summary.published == 2
    assert second_summary.queued == 2
    assert runtime._window_batches == {}
    assert runtime._lanes == {}


@pytest.mark.asyncio
async def test_scheduler_supersession_records_window_batch_terminal_traces() -> None:
    provider = _CancellationResistantBatchProvider()
    traces = _TraceSink()
    runtime, publisher, room = _runtime(provider, trace_recorder=traces)
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer("viewer-1"), _viewer("viewer-2")))
    summaries = []

    class Executor:
        async def react(self, observation: Observation) -> ReactionResult:
            summary = await runtime.dispatch_window_batch(
                wave=_wave(observation.observation_id),
                decision=_decision(
                    observation.observation_id,
                    "viewer-1",
                    "viewer-2",
                ),
                pool=pool,
                runtime=_runtime_context(),
            )
            summaries.append(summary)
            return ReactionResult(published_events=(), validations=())

    scheduler = LatestWinsReactionScheduler(
        executor=Executor(),
        session_tasks=_SessionTasks(),
        clock=_Clock(),
    )
    old = await scheduler.submit(
        _observation("old", source=RoomEventSource.USER_TEXT)
    )
    await provider.first_started.wait()
    new = await scheduler.submit(
        _observation("new", source=RoomEventSource.USER_TEXT)
    )

    assert await asyncio.wait_for(old, timeout=0.2) is None
    assert await asyncio.wait_for(new, timeout=0.2) is not None
    await asyncio.wait_for(provider.first_cancelled.wait(), timeout=0.2)
    provider.release_first.set()
    await asyncio.sleep(0)

    assert summaries[0].superseded == 2
    assert summaries[0].completed == 2
    assert summaries[1].published == 1
    assert summaries[1].silenced == 1
    old_traces = [
        trace
        for trace in traces.traces
        if getattr(trace, "observation_id", None) == "old"
    ]
    assert len(old_traces) == 2
    assert all(trace.response_status is TraceResponseStatus.CANCELLED for trace in old_traces)
    assert all(
        trace.stale_or_cancel_reason == "superseded_by_scheduler"
        for trace in old_traces
    )
    assert [event.text for event in publisher.events] == ["new"]
    assert [event.text for event in room.events] == ["new"]


@pytest.mark.asyncio
async def test_window_batch_cancellation_traces_viewers_not_yet_sequence_claimed() -> None:
    provider = _BatchProvider()
    traces = _TraceSink()
    claimer = _GatedSequenceClaimer()
    runtime, publisher, room = _runtime(
        provider,
        trace_recorder=traces,
        sequence_claimer=claimer,
    )
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=_wave("claiming"),
            decision=_decision("claiming", "viewer-1", "viewer-2"),
            pool=SimpleNamespace(
                viewers=(_viewer("viewer-1"), _viewer("viewer-2"))
            ),
            runtime=_runtime_context(),
        )
    )
    await claimer.second_started.wait()

    dispatch.cancel()
    summary = await asyncio.wait_for(dispatch, timeout=0.2)

    assert summary.selected == 2
    assert summary.superseded == 2
    assert summary.dispatched == 1
    assert summary.completed == 1
    assert provider.requests == []
    assert publisher.events == []
    assert room.events == []
    request_traces = [
        trace
        for trace in traces.traces
        if getattr(trace, "observation_id", None) == "claiming"
    ]
    assert {trace.viewer_instance_id for trace in request_traces} == {
        "viewer-1",
        "viewer-2",
    }
    assert all(
        trace.response_status is TraceResponseStatus.CANCELLED
        for trace in request_traces
    )
    by_viewer = {trace.viewer_instance_id: trace for trace in request_traces}
    assert by_viewer["viewer-1"].provider.dispatched_at_ms == 100
    assert by_viewer["viewer-1"].provider.completed_at_ms == 100
    assert by_viewer["viewer-2"].provider.dispatched_at_ms is None
    assert by_viewer["viewer-2"].provider.completed_at_ms is None


@pytest.mark.asyncio
async def test_window_batch_cancel_after_claim_rejection_keeps_stale_count() -> None:
    provider = _BatchProvider()
    traces = _TraceSink()
    claimer = _RejectThenGateSequenceClaimer()
    runtime, publisher, room = _runtime(
        provider,
        trace_recorder=traces,
        sequence_claimer=claimer,
    )
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=_wave("reject-then-cancel"),
            decision=_decision(
                "reject-then-cancel",
                "viewer-1",
                "viewer-2",
            ),
            pool=SimpleNamespace(
                viewers=(_viewer("viewer-1"), _viewer("viewer-2"))
            ),
            runtime=_runtime_context(),
        )
    )
    await claimer.second_started.wait()

    dispatch.cancel()
    summary = await asyncio.wait_for(dispatch, timeout=0.2)

    assert summary.selected == 2
    assert summary.stale == 1
    assert summary.superseded == 1
    assert summary.dispatched == 0
    assert summary.completed == 0
    assert provider.requests == []
    assert publisher.events == []
    assert room.events == []
    by_viewer = {
        trace.viewer_instance_id: trace
        for trace in traces.traces
        if getattr(trace, "observation_id", None) == "reject-then-cancel"
    }
    assert by_viewer["viewer-1"].response_status is TraceResponseStatus.STALE
    assert by_viewer["viewer-1"].stale_or_cancel_reason == (
        "viewer_sequence_claim_rejected"
    )
    assert by_viewer["viewer-2"].response_status is TraceResponseStatus.CANCELLED
    assert by_viewer["viewer-2"].stale_or_cancel_reason == "superseded_by_scheduler"


@pytest.mark.asyncio
async def test_window_batch_terminal_counts_do_not_require_trace_recorder() -> None:
    provider = _BatchProvider()
    claimer = _RejectThenGateSequenceClaimer()
    runtime, _, _ = _runtime(provider, sequence_claimer=claimer)
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=_wave("no-trace-recorder"),
            decision=_decision(
                "no-trace-recorder",
                "viewer-1",
                "viewer-2",
            ),
            pool=SimpleNamespace(
                viewers=(_viewer("viewer-1"), _viewer("viewer-2"))
            ),
            runtime=_runtime_context(),
        )
    )
    await claimer.second_started.wait()

    dispatch.cancel()
    summary = await asyncio.wait_for(dispatch, timeout=0.2)

    assert summary.selected == 2
    assert summary.stale == 1
    assert summary.superseded == 1
    assert summary.dispatched == 0
    assert summary.completed == 0


@pytest.mark.asyncio
async def test_newer_window_batch_cancels_old_provider_with_zero_side_effects() -> None:
    provider = _CancellationResistantBatchProvider()
    runtime, publisher, room = _runtime(provider)
    await runtime.start_session("session-1")
    pool = SimpleNamespace(viewers=(_viewer("viewer-1"), _viewer("viewer-2")))

    first = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=_wave("old"),
            decision=_decision("old", "viewer-1", "viewer-2"),
            pool=pool,
            runtime=_runtime_context(),
        )
    )
    await provider.first_started.wait()
    second = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=_wave("new"),
            decision=_decision("new", "viewer-1", "viewer-2"),
            pool=pool,
            runtime=_runtime_context(),
        )
    )

    await asyncio.wait_for(provider.first_cancelled.wait(), timeout=0.2)
    first_summary = await asyncio.wait_for(first, timeout=0.2)
    second_summary = await asyncio.wait_for(second, timeout=0.2)
    provider.release_first.set()
    await asyncio.sleep(0)

    assert first_summary.superseded == 2
    assert second_summary.published == 1
    assert second_summary.silenced == 1
    assert [event.text for event in publisher.events] == ["new"]
    assert [event.text for event in room.events] == ["new"]


@pytest.mark.asyncio
async def test_stop_session_cancels_window_batch_without_waiting_for_provider() -> None:
    provider = _CancellationResistantBatchProvider()
    runtime, publisher, room = _runtime(provider)
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch_window_batch(
            wave=_wave("old"),
            decision=_decision("old", "viewer-1", "viewer-2"),
            pool=SimpleNamespace(
                viewers=(_viewer("viewer-1"), _viewer("viewer-2"))
            ),
            runtime=_runtime_context(),
        )
    )
    await provider.first_started.wait()

    await asyncio.wait_for(runtime.stop_session("session-1"), timeout=0.2)
    summary = await asyncio.wait_for(dispatch, timeout=0.2)
    provider.release_first.set()
    await asyncio.sleep(0)

    assert summary.cancelled == 2
    assert not publisher.events
    assert not room.events
    assert runtime._window_batches == {}
    assert runtime._lanes == {}


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
