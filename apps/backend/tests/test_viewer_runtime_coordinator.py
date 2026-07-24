import asyncio

import pytest

from advx_backend.application.director_service import DirectorOutcome, DirectorService
from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.viewer_pool_service import ViewerPoolSnapshot
from advx_backend.application.viewer_runtime import ViewerDispatchSummary
from advx_backend.application.viewer_runtime_coordinator import (
    FrameMetadata,
    ViewerRuntimeCoordinator,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    DirectorFailureMode,
    ProviderRuntimeSpec,
    Room,
    RuntimeSettings,
)
from advx_backend.domain.crowd_decision import CrowdDecision, DecisionSource
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation import FrameRef, Observation
from advx_backend.domain.observation_wave import (
    FrameBundleSettings,
    FrameSelectionStrategy,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import (
    AmbienceMode,
    ModeDefinition,
    PersonaTemplate,
    ResponseRange,
)
from advx_backend.domain.room import RoomEvent, RoomEventSource


def spec(
    *,
    revision: int = 1,
    ttl_ms: int = 500,
    failure_mode: DirectorFailureMode = DirectorFailureMode.STRICT,
    visual_mode: ViewerVisualInputMode = ViewerVisualInputMode.DIRECT_FRAMES,
    frame_bundle: FrameBundleSettings | None = None,
    ambience: AmbienceMode = AmbienceMode.NATURAL,
    screen_change_threshold: float = 0.2,
    screen_change_cooldown_ms: int = 2_000,
    ambient_tick_cooldown_ms: int = 5_000,
    max_consecutive_ambient_waves: int = 2,
) -> CanonicalRuntimeSpec:
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
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1",
        revision=revision,
        viewer_count=1,
        persona_ids=["persona-1"],
        persona_weights={"persona-1": 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=0, maximum=1),
        ambience=ambience,
    )
    return CanonicalRuntimeSpec(
        config_revision=revision,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=0,
            updated_at_ms=revision,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
        settings=RuntimeSettings(
            frame_bundle=frame_bundle or FrameBundleSettings(),
            viewer_request_ttl_ms=ttl_ms,
            director_failure_mode=failure_mode,
            viewer_visual_input_mode=visual_mode,
            screen_change_threshold=screen_change_threshold,
            screen_change_cooldown_ms=screen_change_cooldown_ms,
            ambient_tick_cooldown_ms=ambient_tick_cooldown_ms,
            max_consecutive_ambient_waves=max_consecutive_ambient_waves,
        ),
    )


def state(
    *,
    revision: int = 1,
    epoch: int = 1,
    failure_mode: DirectorFailureMode = DirectorFailureMode.STRICT,
    visual_mode: ViewerVisualInputMode = ViewerVisualInputMode.DIRECT_FRAMES,
    frame_bundle: FrameBundleSettings | None = None,
    ambience: AmbienceMode = AmbienceMode.NATURAL,
    screen_change_threshold: float = 0.2,
    screen_change_cooldown_ms: int = 2_000,
    ambient_tick_cooldown_ms: int = 5_000,
    max_consecutive_ambient_waves: int = 2,
) -> CommittedRuntime:
    runtime_spec = spec(
        revision=revision,
        failure_mode=failure_mode,
        visual_mode=visual_mode,
        frame_bundle=frame_bundle,
        ambience=ambience,
        screen_change_threshold=screen_change_threshold,
        screen_change_cooldown_ms=screen_change_cooldown_ms,
        ambient_tick_cooldown_ms=ambient_tick_cooldown_ms,
        max_consecutive_ambient_waves=max_consecutive_ambient_waves,
    )
    return CommittedRuntime(
        session_id="session-1",
        spec=runtime_spec,
        audience_epoch=epoch,
        pool=ViewerPoolSnapshot(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=epoch,
            mode_id="mode-1",
            session_seed="seed",
            viewers=[],
        ),
    )


def decision(wave: object) -> CrowdDecision:
    return CrowdDecision(
        decision_id="decision-1",
        room_id=wave.room_id,
        session_id=wave.session_id,
        audience_epoch=wave.audience_epoch,
        observation_id=wave.observation_id,
        selected_viewer_ids=[],
        created_at_ms=wave.created_at_ms,
        expires_at_ms=wave.deadline_at_ms,
    )


class RecordingDirector:
    def __init__(self, state_store: RuntimeStateStore | None = None) -> None:
        self.calls: list[tuple[object, object, object]] = []
        self.state_store = state_store

    async def decide(self, *, wave: object, pool: object, runtime: object) -> object:
        self.calls.append((wave, pool, runtime))
        if self.state_store is not None:
            await self.state_store.replace(state(revision=2, epoch=2))
        return DirectorOutcome(decision=decision(wave))


class RecordingViewerRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object, object]] = []
        self.observation_traces: list[dict[str, object]] = []

    async def dispatch(
        self,
        *,
        wave: object,
        decision: object,
        pool: object,
        runtime: object,
    ) -> ViewerDispatchSummary:
        self.calls.append((wave, decision, pool, runtime))
        return ViewerDispatchSummary()

    def record_observation_trace(self, **trace: object) -> None:
        self.observation_traces.append(trace)


class MetadataResolver:
    async def resolve(
        self,
        *,
        session_id: str,
        frame: FrameRef,
    ) -> FrameMetadata | None:
        assert session_id == "session-1"
        if frame.frame_id == "incomplete":
            return None
        return FrameMetadata(
            width=1920,
            height=1080,
            encoding="image/jpeg",
            content_hash="a" * 64,
        )


class ScoredMetadataResolver:
    def __init__(self, score: float) -> None:
        self.score = score

    async def resolve(
        self,
        *,
        session_id: str,
        frame: FrameRef,
    ) -> FrameMetadata:
        del session_id
        return FrameMetadata(
            width=1920,
            height=1080,
            encoding="image/jpeg",
            content_hash=frame.frame_id[0] * 64,
            change_score=self.score,
        )


class FailingDirector:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, **_: object) -> object:
        self.calls += 1
        raise RuntimeError("strict Director failed")


class FailingDirectorProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, _: object) -> object:
        self.calls += 1
        raise ConnectionError("Director unavailable")


class OneViewerBudget:
    def maximum(self, **_: object) -> int:
        return 1


class RecordingFallback:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, *, wave: object, **_: object) -> CrowdDecision:
        self.calls += 1
        return decision(wave)


class FixedClock:
    def now_ms(self) -> int:
        return 100


class RecordingMemoryReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], int]] = []
        self.slice = RoomMemorySlice(
            room_id="room-1",
            memory_revision=3,
            memory_ids=["memory-1"],
        )

    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice:
        self.calls.append((room_id, event_ids, limit))
        return self.slice


class RecordingSummarizer:
    def __init__(self, summary: str = "shared visual summary") -> None:
        self.summary = summary
        self.calls: list[tuple[object, object, object]] = []

    async def summarize(
        self,
        *,
        wave: object,
        frame_bundle: object,
        runtime: object,
    ) -> str:
        self.calls.append((wave, frame_bundle, runtime))
        return self.summary


class MemeDirector(RecordingDirector):
    async def decide(self, *, wave: object, pool: object, runtime: object) -> object:
        self.calls.append((wave, pool, runtime))
        candidate = MemeCandidate(
            candidate_id="candidate-1",
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            namespace_id="mode-1",
            text="possible meme",
            evidence_event_ids=["event-1"],
            created_at_ms=wave.created_at_ms,
        )
        return DirectorOutcome(decision=decision(wave), meme_candidate=candidate)


class RecordingMemeSink:
    def __init__(self) -> None:
        self.candidates: list[MemeCandidate] = []

    async def commit_candidate(self, candidate: MemeCandidate) -> object:
        self.candidates.append(candidate)
        return object()


class RecordingExtractionSink:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[tuple[object, object, object, object]] = []

    async def extract_after_wave(
        self,
        *,
        wave: object,
        decision: object,
        dispatch: object,
        runtime: object,
    ) -> None:
        self.order.append("extract")
        self.calls.append((wave, decision, dispatch, runtime))


class OrderedViewerRuntime(RecordingViewerRuntime):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    async def dispatch(self, **values: object) -> ViewerDispatchSummary:
        self.order.append("dispatch")
        self.calls.append(
            (
                values["wave"],
                values["decision"],
                values["pool"],
                values["runtime"],
            )
        )
        return ViewerDispatchSummary(published=1)


class TelemetryViewerRuntime(RecordingViewerRuntime):
    async def dispatch(self, **values: object) -> ViewerDispatchSummary:
        self.calls.append(
            (
                values["wave"],
                values["decision"],
                values["pool"],
                values["runtime"],
            )
        )
        return ViewerDispatchSummary(
            selected=12,
            queued=4,
            dispatched=10,
            completed=10,
            published=1,
            silenced=2,
            rejected=3,
            expired=4,
            failed=5,
            stale=6,
            cancelled=7,
            superseded=8,
            retry=9,
        )


def observation(
    *,
    frames: tuple[FrameRef, ...] = (),
    created_at_ms: int = 100,
) -> Observation:
    return Observation(
        session_id="session-1",
        observation_id="observation-1",
        created_at_ms=created_at_ms,
        frames=frames,
        room_events=(
            RoomEvent(
                event_id="event-1",
                session_id="session-1",
                sequence=1,
                source_type=RoomEventSource.USER_TEXT,
                created_at_ms=90,
                text="hello",
            ),
        ),
        trigger_event_ids=("event-1",),
        trigger_frame_ids=tuple(frame.frame_id for frame in frames),
    )


@pytest.mark.asyncio
async def test_snapshot_and_public_context_are_frozen_for_the_whole_wave() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    director = RecordingDirector(store)
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=viewer_runtime,
    )

    result = await coordinator.react(observation())

    assert len(director.calls) == 1
    assert len(viewer_runtime.calls) == 1
    dispatched_wave, _, dispatched_pool, dispatched_runtime = viewer_runtime.calls[0]
    assert dispatched_wave.audience_epoch == 1
    assert dispatched_pool.audience_epoch == 1
    assert dispatched_runtime.canonical_runtime_spec.config_revision == 1
    assert dispatched_runtime.public_context_event_ids == ("event-1",)
    assert result.wave is dispatched_wave


@pytest.mark.asyncio
async def test_semantically_identical_input_has_zero_repeated_side_effects() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    director = RecordingDirector()
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=viewer_runtime,
    )

    first = await coordinator.react(observation())
    duplicate = await coordinator.react(observation(created_at_ms=101))

    assert first.skipped is False
    assert duplicate.skipped is True
    assert duplicate.semantic_duplicate is True
    assert len(director.calls) == 1
    assert len(viewer_runtime.calls) == 1


@pytest.mark.asyncio
async def test_semantic_dedup_allows_correction_revision_and_expires_by_time() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    director = RecordingDirector()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=RecordingViewerRuntime(),
        semantic_dedup_ttl_ms=100,
    )

    def voice(event_id: str, revision: int, created_at_ms: int) -> Observation:
        event = RoomEvent(
            event_id=event_id,
            session_id="session-1",
            sequence=int(event_id.removeprefix("event-")),
            source_type=RoomEventSource.USER_VOICE,
            created_at_ms=created_at_ms,
            text="same utterance",
            payload={"utterance_id": "utterance-1", "revision": revision},
        )
        return Observation(
            session_id="session-1",
            observation_id=f"observation-{event_id}",
            created_at_ms=created_at_ms,
            room_events=(event,),
            trigger_event_ids=(event_id,),
        )

    first = await coordinator.react(voice("event-1", 1, 100))
    duplicate = await coordinator.react(voice("event-2", 1, 101))
    corrected = await coordinator.react(voice("event-3", 2, 102))
    expired = await coordinator.react(voice("event-4", 1, 201))

    assert first.skipped is False
    assert duplicate.semantic_duplicate is True
    assert corrected.skipped is False
    assert expired.skipped is False
    assert len(director.calls) == 3


@pytest.mark.asyncio
async def test_structured_target_is_carried_from_event_payload_into_wave() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    director = RecordingDirector()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=RecordingViewerRuntime(),
    )
    targeted = Observation(
        session_id="session-1",
        observation_id="targeted",
        created_at_ms=100,
        room_events=(
            RoomEvent(
                event_id="event-target",
                session_id="session-1",
                sequence=1,
                source_type=RoomEventSource.USER_TEXT,
                created_at_ms=90,
                text="@Viewer hello",
                payload={"target_viewer_id": "viewer-1"},
            ),
        ),
        trigger_event_ids=("event-target",),
    )

    result = await coordinator.react(targeted)

    assert result.wave is not None
    assert result.wave.target_viewer_id == "viewer-1"
    assert result.wave.target_persona_id is None


@pytest.mark.asyncio
async def test_old_public_target_is_not_replayed_when_delta_is_untargeted() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=RecordingViewerRuntime(),
    )
    old = RoomEvent(
        event_id="event-old",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.USER_TEXT,
        created_at_ms=80,
        text="@Viewer old",
        payload={"target_viewer_id": "viewer-1"},
    )
    current = RoomEvent(
        event_id="event-current",
        session_id="session-1",
        sequence=2,
        source_type=RoomEventSource.USER_TEXT,
        created_at_ms=90,
        text="ordinary broadcast",
    )

    result = await coordinator.react(
        Observation(
            session_id="session-1",
            observation_id="delta",
            created_at_ms=100,
            room_events=(old, current),
            trigger_event_ids=("event-current",),
        )
    )

    assert result.wave is not None
    assert result.wave.event_ids == ["event-old", "event-current"]
    assert result.wave.trigger_event_ids == ["event-current"]
    assert result.wave.target_viewer_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["frame", "ambient"])
async def test_empty_event_delta_never_replays_old_target(
    kind: str,
) -> None:
    store = RuntimeStateStore()
    await store.activate(
        state(
            ambience=(
                AmbienceMode.CONTINUOUS
                if kind == "ambient"
                else AmbienceMode.NATURAL
            ),
            ambient_tick_cooldown_ms=1,
        )
    )
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=RecordingViewerRuntime(),
        frame_metadata=(
            ScoredMetadataResolver(1.0)
            if kind == "frame"
            else None
        ),
    )
    old = RoomEvent(
        event_id="event-old",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.USER_TEXT,
        created_at_ms=80,
        text="@Viewer old",
        payload={"target_viewer_id": "viewer-1"},
    )
    frame = (
        (FrameRef("a-frame", 100, "image/jpeg", "frame:new"),)
        if kind == "frame"
        else ()
    )

    result = await coordinator.react(
        Observation(
            session_id="session-1",
            observation_id=kind,
            created_at_ms=100,
            frames=frame,
            room_events=(old,),
            trigger_frame_ids=tuple(item.frame_id for item in frame),
        )
    )

    assert result.wave is not None
    assert result.wave.target_viewer_id is None
    assert result.wave.trigger_event_ids == []


@pytest.mark.asyncio
async def test_failed_wave_rolls_back_semantic_dedup_and_screen_cooldown() -> None:
    store = RuntimeStateStore()
    await store.activate(
        state(screen_change_threshold=0.5, screen_change_cooldown_ms=1_000)
    )

    class FailOnceDirector(RecordingDirector):
        async def decide(self, **values: object) -> object:
            self.calls.append((values["wave"], values["pool"], values["runtime"]))
            if len(self.calls) == 1:
                raise RuntimeError("first attempt fails")
            return DirectorOutcome(decision=decision(values["wave"]))

    director = FailOnceDirector()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=RecordingViewerRuntime(),
        frame_metadata=ScoredMetadataResolver(0.8),
    )

    def screen(observation_id: str) -> Observation:
        return Observation(
            session_id="session-1",
            observation_id=observation_id,
            created_at_ms=100,
            frames=(FrameRef("a-screen", 100, "image/jpeg", "frame:screen"),),
            trigger_frame_ids=("a-screen",),
        )

    failed = await coordinator.react(screen("attempt-1"))
    retried = await coordinator.react(screen("attempt-2"))

    assert failed.director_failed is True
    assert retried.skipped is False
    assert retried.semantic_duplicate is False
    assert len(director.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_summary",
    [
        ViewerDispatchSummary(failed=1),
        ViewerDispatchSummary(rejected=1),
        ViewerDispatchSummary(expired=1),
        ViewerDispatchSummary(stale=1),
    ],
)
async def test_terminal_only_dispatch_rolls_back_admission_for_retry(
    failed_summary: ViewerDispatchSummary,
) -> None:
    store = RuntimeStateStore()
    await store.activate(state())

    class SelectingDirector:
        async def decide(self, *, wave: object, **_: object) -> DirectorOutcome:
            selected = decision(wave).model_copy(
                update={"selected_viewer_ids": ["viewer-1"]}
            )
            return DirectorOutcome(decision=selected)

    class RetryDispatchRuntime(RecordingViewerRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.summaries = [
                failed_summary,
                ViewerDispatchSummary(
                    selected=1,
                    dispatched=1,
                    completed=1,
                    silenced=1,
                ),
            ]

        async def dispatch(self, **values: object) -> ViewerDispatchSummary:
            self.calls.append(
                (
                    values["wave"],
                    values["decision"],
                    values["pool"],
                    values["runtime"],
                )
            )
            return self.summaries.pop(0)

    viewer_runtime = RetryDispatchRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=SelectingDirector(),
        viewer_runtime=viewer_runtime,
    )

    first = await coordinator.react(observation())
    retry = await coordinator.react(observation(created_at_ms=101))

    assert first.skipped is False
    assert retry.skipped is False
    assert len(viewer_runtime.calls) == 2


@pytest.mark.asyncio
async def test_stop_cancels_session_memory_extraction_task() -> None:
    store = RuntimeStateStore()
    await store.activate(state())

    class BlockingSink:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def extract_after_wave(self, **_: object) -> None:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    sink = BlockingSink()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=OrderedViewerRuntime([]),
        memory_extraction_sink=sink,
        background_task_timeout_ms=50,
    )

    await coordinator.react(observation())
    await asyncio.wait_for(sink.started.wait(), timeout=1)
    await coordinator.stop_session("session-1")

    assert sink.cancelled.is_set()
    assert coordinator._background_tasks == set()


@pytest.mark.asyncio
async def test_stop_detaches_cancellation_resistant_memory_task_after_bounded_wait() -> None:
    store = RuntimeStateStore()
    await store.activate(state())

    class CancellationResistantSink:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()
            self.cancellations = 0
            self.effects = 0
            self.accepted: bool | None = None

        async def extract_after_wave_fenced(
            self,
            *,
            commit_effect,
            **_: object,
        ) -> None:
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancellations += 1
            self.accepted, _ = await commit_effect(self._commit)
            self.finished.set()

        async def _commit(self) -> object:
            self.effects += 1
            return object()

    sink = CancellationResistantSink()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=OrderedViewerRuntime([]),
        memory_extraction_sink=sink,
        background_task_timeout_ms=20,
    )

    await coordinator.react(observation())
    await asyncio.wait_for(sink.started.wait(), timeout=1)
    await store.stop("session-1")
    await asyncio.wait_for(coordinator.stop_session("session-1"), timeout=0.2)

    assert sink.cancellations >= 2
    assert coordinator._background_tasks == set()
    sink.release.set()
    await asyncio.wait_for(sink.finished.wait(), timeout=1)
    assert sink.accepted is False
    assert sink.effects == 0


@pytest.mark.asyncio
async def test_runtime_telemetry_is_available_as_an_application_snapshot() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=TelemetryViewerRuntime(),
    )

    await coordinator.react(observation())

    snapshot = coordinator.telemetry_snapshot("session-1")
    assert snapshot.selected == 12
    assert snapshot.queued == 4
    assert snapshot.dispatched == 10
    assert snapshot.completed == 10
    assert snapshot.silence == 2
    assert snapshot.published == 1
    assert snapshot.rejected == 3
    assert snapshot.expired == 4
    assert snapshot.failed == 5
    assert snapshot.stale == 6
    assert snapshot.cancelled == 7
    assert snapshot.superseded == 8
    assert snapshot.retry == 9


@pytest.mark.asyncio
async def test_screen_trigger_requires_significance_and_respects_cooldown() -> None:
    store = RuntimeStateStore()
    await store.activate(
        state(screen_change_threshold=0.5, screen_change_cooldown_ms=1_000)
    )
    director = RecordingDirector()
    low = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=RecordingViewerRuntime(),
        frame_metadata=ScoredMetadataResolver(0.49),
    )
    low_result = await low.react(
        Observation(
            session_id="session-1",
            observation_id="low",
            created_at_ms=100,
            frames=(FrameRef("a-low", 100, "image/jpeg", "frame:low"),),
            trigger_frame_ids=("a-low",),
        )
    )
    assert low_result.skipped is True
    assert director.calls == []

    high = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=RecordingViewerRuntime(),
        frame_metadata=ScoredMetadataResolver(0.5),
    )
    admitted = await high.react(
        Observation(
            session_id="session-1",
            observation_id="high-1",
            created_at_ms=100,
            frames=(FrameRef("b-high", 100, "image/jpeg", "frame:high"),),
            trigger_frame_ids=("b-high",),
        )
    )
    cooldown = await high.react(
        Observation(
            session_id="session-1",
            observation_id="high-2",
            created_at_ms=101,
            frames=(FrameRef("c-high", 101, "image/jpeg", "frame:new"),),
            trigger_frame_ids=("c-high",),
        )
    )

    assert admitted.wave is not None
    assert cooldown.skipped is True
    assert cooldown.semantic_duplicate is False
    assert len(director.calls) == 1


@pytest.mark.asyncio
async def test_ambient_policy_is_continuous_only_bounded_and_reset_by_real_input() -> None:
    store = RuntimeStateStore()
    await store.activate(
        state(
            ambience=AmbienceMode.CONTINUOUS,
            ambient_tick_cooldown_ms=1_000,
            max_consecutive_ambient_waves=2,
        )
    )
    director = RecordingDirector()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=RecordingViewerRuntime(),
    )

    first = await coordinator.react(Observation("session-1", "a1", 100))
    cooling = await coordinator.react(Observation("session-1", "a2", 500))
    second = await coordinator.react(Observation("session-1", "a3", 1_100))
    forced_quiet = await coordinator.react(Observation("session-1", "a4", 2_100))
    real = await coordinator.react(observation(created_at_ms=2_200))
    reset = await coordinator.react(Observation("session-1", "a5", 3_200))

    assert first.skipped is False
    assert cooling.skipped is True
    assert second.skipped is False
    assert forced_quiet.skipped is True
    assert real.skipped is False
    assert reset.skipped is False
    assert len(director.calls) == 4


@pytest.mark.asyncio
async def test_missing_frame_metadata_never_fabricates_a_frame_bundle() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    director = RecordingDirector()
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=viewer_runtime,
    )
    frame = FrameRef(
        frame_id="legacy-frame",
        created_at_ms=95,
        mime_type="image/jpeg",
        data_ref="frame:1",
    )

    result = await coordinator.react(observation(frames=(frame,)))

    assert result.wave is not None
    assert result.wave.frame_bundle is None
    assert result.wave.deadline_at_ms == 600


@pytest.mark.asyncio
async def test_resolver_admits_only_frames_with_complete_metadata() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=RecordingViewerRuntime(),
        frame_metadata=MetadataResolver(),
    )
    frames = (
        FrameRef("incomplete", 94, "image/jpeg", "frame:0"),
        FrameRef("complete", 95, "image/jpeg", "frame:1"),
    )

    result = await coordinator.react(observation(frames=frames))

    assert result.wave is not None
    assert result.wave.frame_bundle is not None
    assert [item.frame_id for item in result.wave.frame_bundle.frames] == ["complete"]
    assert result.wave.frame_bundle.frames[0].content_hash == "a" * 64


@pytest.mark.asyncio
async def test_next_wave_reads_hot_applied_frame_bundle_spec() -> None:
    store = RuntimeStateStore()
    await store.activate(
        state(
            frame_bundle=FrameBundleSettings(
                frame_bundle_size=1,
                frame_window_ms=100,
                frame_selection_strategy=FrameSelectionStrategy.LATEST_N,
            )
        )
    )
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=RecordingViewerRuntime(),
        frame_metadata=MetadataResolver(),
    )
    frames = (
        FrameRef("first", 100, "image/jpeg", "frame:first"),
        FrameRef("middle", 200, "image/jpeg", "frame:middle"),
        FrameRef("latest", 300, "image/jpeg", "frame:latest"),
    )

    initial = await coordinator.react(observation(frames=frames, created_at_ms=300))
    await store.replace(
        state(
            revision=2,
            epoch=2,
            frame_bundle=FrameBundleSettings(
                frame_bundle_size=2,
                frame_window_ms=1_000,
                frame_selection_strategy=FrameSelectionStrategy.EVENLY_SPACED,
                frame_max_dimension=640,
                frame_quality=55,
            ),
        )
    )
    updated = await coordinator.react(observation(frames=frames, created_at_ms=300))

    assert initial.wave is not None
    assert initial.wave.frame_bundle is not None
    assert [item.frame_id for item in initial.wave.frame_bundle.frames] == ["latest"]
    assert updated.wave is not None
    assert updated.wave.frame_bundle is not None
    assert [item.frame_id for item in updated.wave.frame_bundle.frames] == [
        "first",
        "latest",
    ]
    assert updated.wave.frame_bundle.settings.frame_max_dimension == 640
    assert updated.wave.frame_bundle.settings.frame_quality == 55


@pytest.mark.asyncio
async def test_strict_director_failure_makes_the_wave_quiet() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    director = FailingDirector()
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=viewer_runtime,
    )

    result = await coordinator.react(observation())

    assert director.calls == 1
    assert result.director_failed is True
    assert result.published_events == ()
    assert viewer_runtime.calls == []
    assert len(viewer_runtime.observation_traces) == 1
    trace = viewer_runtime.observation_traces[0]
    assert trace["failure_reason"] == "director_failed"
    assert trace["status"].value == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate_update",
    [
        {"namespace_id": "mode-other"},
        {"observation_id": "observation-other"},
        {"evidence_event_ids": ["event-other"]},
        {"evidence_frame_indexes": [0]},
    ],
)
async def test_invalid_meme_candidate_has_no_output_or_persistence_side_effects(
    candidate_update: dict[str, object],
) -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    candidate = MemeCandidate(
        candidate_id="candidate-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        namespace_id="mode-1",
        text="candidate",
        evidence_event_ids=["event-1"],
        created_at_ms=100,
    ).model_copy(update=candidate_update)

    class CandidateDirector:
        async def decide(self, *, wave: object, **_: object) -> DirectorOutcome:
            return DirectorOutcome(
                decision=decision(wave),
                meme_candidate=candidate,
            )

    viewer_runtime = RecordingViewerRuntime()
    meme_sink = RecordingMemeSink()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=CandidateDirector(),
        viewer_runtime=viewer_runtime,
        meme_sink=meme_sink,
    )

    result = await coordinator.react(observation())

    assert result.meme_failed is True
    assert result.published_events == ()
    assert viewer_runtime.calls == []
    assert meme_sink.candidates == []
    assert viewer_runtime.observation_traces[0]["failure_reason"] == (
        "meme_candidate_invalid"
    )


@pytest.mark.asyncio
async def test_zero_selection_records_an_observation_wave_trace() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=viewer_runtime,
    )

    result = await coordinator.react(observation())

    assert result.decision is not None
    assert result.decision.selected_viewer_ids == []
    assert len(viewer_runtime.observation_traces) == 1
    trace = viewer_runtime.observation_traces[0]
    assert trace["decision"] is result.decision
    assert trace["status"].value == "empty"


@pytest.mark.asyncio
async def test_resilient_director_failure_uses_fallback_then_dispatches() -> None:
    store = RuntimeStateStore()
    await store.activate(state(failure_mode=DirectorFailureMode.RESILIENT))
    provider = FailingDirectorProvider()
    fallback = RecordingFallback()
    director = DirectorService(
        provider=provider,
        budget_policy=OneViewerBudget(),
        fallback=fallback,
        clock=FixedClock(),
    )
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=viewer_runtime,
    )

    result = await coordinator.react(observation())

    assert provider.calls == 1
    assert fallback.calls == 1
    assert len(viewer_runtime.calls) == 1
    assert result.decision is not None
    assert result.decision.decision_source is DecisionSource.FALLBACK


@pytest.mark.asyncio
async def test_direct_frames_mode_does_not_call_shared_summarizer() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    summarizer = RecordingSummarizer()
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=viewer_runtime,
        frame_metadata=MetadataResolver(),
        visual_summarizer=summarizer,
    )
    frame = FrameRef("complete", 95, "image/jpeg", "frame:1")

    result = await coordinator.react(observation(frames=(frame,)))

    assert result.wave is not None
    assert result.wave.visual_input_mode is ViewerVisualInputMode.DIRECT_FRAMES
    assert result.wave.frame_bundle is not None
    assert summarizer.calls == []


@pytest.mark.asyncio
async def test_shared_summary_runs_once_and_dispatches_without_frames() -> None:
    store = RuntimeStateStore()
    await store.activate(state(visual_mode=ViewerVisualInputMode.SHARED_SUMMARY))
    summarizer = RecordingSummarizer()
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=viewer_runtime,
        frame_metadata=MetadataResolver(),
        visual_summarizer=summarizer,
    )
    frame = FrameRef("complete", 95, "image/jpeg", "frame:1")

    result = await coordinator.react(observation(frames=(frame,)))

    assert len(summarizer.calls) == 1
    summarized_wave, summarized_bundle, _ = summarizer.calls[0]
    assert summarized_wave.frame_bundle is summarized_bundle
    assert result.wave is not None
    assert result.wave.visual_input_mode is ViewerVisualInputMode.SHARED_SUMMARY
    assert result.wave.frame_bundle is None
    assert result.wave.shared_visual_summary == "shared visual summary"
    assert viewer_runtime.calls[0][0] is result.wave


@pytest.mark.asyncio
async def test_shared_summary_failure_is_quiet_without_direct_fallback() -> None:
    store = RuntimeStateStore()
    await store.activate(state(visual_mode=ViewerVisualInputMode.SHARED_SUMMARY))
    director = RecordingDirector()
    viewer_runtime = RecordingViewerRuntime()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=viewer_runtime,
        frame_metadata=MetadataResolver(),
        visual_summarizer=RecordingSummarizer(summary=" "),
    )
    frame = FrameRef("complete", 95, "image/jpeg", "frame:1")

    result = await coordinator.react(observation(frames=(frame,)))

    assert result.visual_failed is True
    assert director.calls == []
    assert viewer_runtime.calls == []


@pytest.mark.asyncio
async def test_memory_context_is_read_once_shared_and_isolated_from_same_wave_output() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    memory_reader = RecordingMemoryReader()
    order: list[str] = []
    viewer_runtime = OrderedViewerRuntime(order)
    extraction = RecordingExtractionSink(order)
    director = MemeDirector()
    meme_sink = RecordingMemeSink()
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=director,
        viewer_runtime=viewer_runtime,
        memory_reader=memory_reader,
        meme_sink=meme_sink,
        memory_extraction_sink=extraction,
    )

    result = await coordinator.react(observation())
    await coordinator.wait_for_background_tasks()

    assert memory_reader.calls == [("room-1", ("event-1",), 32)]
    director_runtime = director.calls[0][2]
    dispatch_runtime = viewer_runtime.calls[0][3]
    assert director_runtime is dispatch_runtime
    assert dispatch_runtime.room_memory_slice is memory_reader.slice
    assert dispatch_runtime.working_memory.event_ids == ["event-1"]
    assert dispatch_runtime.public_context_event_ids == ("event-1",)
    assert "ai-barrage" not in dispatch_runtime.working_memory.event_ids
    assert [item.candidate_id for item in meme_sink.candidates] == ["candidate-1"]
    assert result.dispatch.published == 1
    assert order == ["dispatch", "extract"]
    assert extraction.calls[0][3] is dispatch_runtime


@pytest.mark.asyncio
async def test_memory_extraction_failure_is_logged_without_blocking_reaction(caplog) -> None:
    store = RuntimeStateStore()
    await store.activate(state())

    class FailingExtractionSink:
        async def extract_after_wave(self, **_: object) -> None:
            raise RuntimeError("memory extraction failed")

    coordinator = ViewerRuntimeCoordinator(
        runtime_state=store,
        director=RecordingDirector(),
        viewer_runtime=RecordingViewerRuntime(),
        memory_extraction_sink=FailingExtractionSink(),
    )

    result = await coordinator.react(observation())
    await coordinator.wait_for_background_tasks()

    assert result.skipped is False
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "viewer memory extraction failed"
    )
    assert record.session_id == "session-1"
    assert record.observation_id == "observation-1"
    assert record.exception_type == "RuntimeError"
    assert record.exc_info is not None
