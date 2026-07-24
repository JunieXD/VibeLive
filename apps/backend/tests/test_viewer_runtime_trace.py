import asyncio
import json
from types import SimpleNamespace

import pytest

from advx_backend.application.debug_service import DebugService
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.application.viewer_trace import build_viewer_request_trace
from advx_backend.contracts.debug import (
    ObservationWaveStatus,
    TraceQuery,
    TraceResponseStatus,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    EvidenceRef,
    EvidenceSource,
    ProviderRuntimeSpec,
    Room,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.memory import (
    RoomLongTermMemory,
    RoomMemorySlice,
    RoomMemoryType,
)
from advx_backend.domain.observation_wave import (
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.viewer import (
    ViewerInstance,
    ViewerInstanceVariant,
    ViewerPrivateState,
)
from advx_backend.infrastructure.logging.trace_store import (
    TraceStore,
    UnsafeTraceArtifactError,
    assert_redacted_artifact,
)


class MutableClock:
    def __init__(self, value: int = 100) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"request-{self.value}"


def spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash="a" * 64,
        display_name="Viewer",
        role="commentator",
        silence_bias=0.2,
        burst_bias=0.4,
        repetition_bias=0.1,
        cooldown_ms=500,
    )
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1",
        revision=1,
        viewer_count=2,
        persona_ids=["persona-1"],
        persona_weights={"persona-1": 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=1, maximum=2),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
        ),
        active_mode_id="mode-1",
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="profile-1",
            director_model="director-model",
            viewer_model="viewer-model",
            memory_model="memory-model",
            visual_summary_model="visual-model",
        ),
    )


def viewer(viewer_id: str = "viewer-1") -> ViewerInstance:
    return ViewerInstance(
        viewer_instance_id=viewer_id,
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        persona_id="persona-1",
        persona_revision=1,
        ordinal=1,
        display_name="Viewer",
        variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="gameplay",
            silence_tendency=0.2,
        ),
        private_state=ViewerPrivateState(
            published_event_ids=["private-published-1"],
            direct_interaction_event_ids=["private-direct-1"],
        ),
        created_at_ms=1,
    )


def wave() -> ObservationWave:
    return ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        created_at_ms=100,
        deadline_at_ms=1_000,
        triggers=[ObservationTrigger.SCREEN_CHANGE],
        event_ids=["event-1"],
        trigger_event_ids=["event-1"],
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="sensitive source summary sk-not-persisted-123456",
    )


def decision(*viewer_ids: str) -> CrowdDecision:
    return CrowdDecision(
        decision_id="decision-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        selected_viewer_ids=list(viewer_ids),
        evidence_event_ids=["event-1"],
        created_at_ms=100,
        expires_at_ms=1_000,
    )


class AcceptingFence:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted

    async def accepts(self, **scope: object) -> bool:
        del scope
        return self.accepted


class AcceptingPipeline:
    def validate(self, *, request: object, response: object) -> object:
        del request, response
        return SimpleNamespace(
            accepted=True,
            event=SimpleNamespace(barrage_id="barrage-1"),
            rejection_reason=None,
        )


class Sink:
    async def publish(self, event: object) -> None:
        del event

    async def append_published_barrage(self, event: object) -> None:
        del event


class PublishedProvider:
    async def generate(self, request: object) -> ViewerGenerationResponse:
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            text="safe result",
            reaction_type="highlight",
            evidence_refs=[EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")],
        )


def build_runtime(
    provider: object,
    *,
    clock: MutableClock,
    recorder: DebugService,
    fence: AcceptingFence | None = None,
) -> ViewerRuntime:
    return ViewerRuntime(
        provider=provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=fence or AcceptingFence(),
        publisher=Sink(),
        room_service=Sink(),
        clock=clock,
        id_generator=SequenceIds(),
        max_in_flight=2,
        trace_recorder=recorder,
    )


def runtime_context() -> object:
    return SimpleNamespace(
        canonical_runtime_spec=spec(),
        public_context_event_ids=["public-1"],
        room_memory_slice=RoomMemorySlice(
            room_id="room-1",
            memory_revision=2,
            memory_ids=["memory-1"],
        ),
        director_budget=SimpleNamespace(
            minimum=0,
            maximum=2,
            forced_viewer_ids=["viewer-1"],
        ),
    )


@pytest.mark.asyncio
async def test_published_request_records_complete_redacted_trace() -> None:
    store = TraceStore()
    recorder = DebugService(store)
    clock = MutableClock()
    runtime = build_runtime(PublishedProvider(), clock=clock, recorder=recorder)
    await runtime.start_session("session-1")

    summary = await runtime.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer(),)),
        runtime=runtime_context(),
    )

    assert summary.published == 1
    trace = store.query().items[0]
    assert trace.response_status is TraceResponseStatus.PUBLISHED
    assert trace.config_hash == runtime_context().canonical_runtime_spec.config_hash()
    assert trace.provider.model_id == "viewer-model"
    assert trace.director_decision.decision_id == "decision-1"
    assert trace.public_context_event_ids == ["public-1"]
    assert trace.private_state_event_ids == [
        "private-published-1",
        "private-direct-1",
    ]
    assert trace.memory.memory_ids == ["memory-1"]
    assert trace.side_effects.published_barrage_id == "barrage-1"
    assert_redacted_artifact(trace)
    serialized = json.dumps(trace.model_dump(mode="json"))
    assert "sensitive source summary" not in serialized
    assert "sk-not-persisted" not in serialized


class RetrySilenceProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("retry")
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.SILENCE,
            reaction_type="silence",
        )


class ExpiringSilenceProvider:
    def __init__(self, clock: MutableClock) -> None:
        self._clock = clock

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self._clock.value = request.deadline_at_ms
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.SILENCE,
            reaction_type="silence",
        )


@pytest.mark.asyncio
async def test_retry_and_late_stale_have_correct_terminal_trace() -> None:
    retry_store = TraceStore()
    retry_runtime = build_runtime(
        RetrySilenceProvider(),
        clock=MutableClock(),
        recorder=DebugService(retry_store),
    )
    await retry_runtime.start_session("session-1")
    await retry_runtime.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer(),)),
        runtime=runtime_context(),
    )
    retry_trace = retry_store.query().items[0]
    assert retry_trace.response_status is TraceResponseStatus.SILENCE
    assert retry_trace.retry_count == 1

    stale_store = TraceStore()
    stale_runtime = build_runtime(
        PublishedProvider(),
        clock=MutableClock(),
        recorder=DebugService(stale_store),
        fence=AcceptingFence(False),
    )
    await stale_runtime.start_session("session-1")
    await stale_runtime.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer(),)),
        runtime=runtime_context(),
    )
    stale_trace = stale_store.query().items[0]
    assert stale_trace.response_status is TraceResponseStatus.STALE
    assert stale_trace.stale_or_cancel_reason == "session_fence_rejected"


@pytest.mark.asyncio
async def test_expired_silence_is_not_accepted() -> None:
    store = TraceStore()
    clock = MutableClock()
    runtime = build_runtime(
        ExpiringSilenceProvider(clock),
        clock=clock,
        recorder=DebugService(store),
    )
    await runtime.start_session("session-1")

    summary = await runtime.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer(),)),
        runtime=runtime_context(),
    )

    assert summary.expired == 1
    assert summary.silenced == 0
    trace = store.query().items[0]
    assert trace.response_status is TraceResponseStatus.EXPIRED
    assert trace.validation.accepted is False
    assert trace.side_effects.published_barrage_id is None


@pytest.mark.asyncio
async def test_silence_must_pass_the_final_session_fence() -> None:
    store = TraceStore()
    runtime = build_runtime(
        RetrySilenceProvider(),
        clock=MutableClock(),
        recorder=DebugService(store),
        fence=AcceptingFence(False),
    )
    await runtime.start_session("session-1")

    summary = await runtime.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer(),)),
        runtime=runtime_context(),
    )

    assert summary.stale == 1
    assert summary.silenced == 0
    trace = store.query().items[0]
    assert trace.response_status is TraceResponseStatus.STALE
    assert trace.validation.accepted is False
    assert trace.stale_or_cancel_reason == "session_fence_rejected"
    assert trace.side_effects.published_barrage_id is None


def test_trace_redacts_long_term_memory_content_and_scanner_rejects_raw_memory() -> None:
    memory = RoomLongTermMemory(
        memory_id="memory-sensitive",
        room_id="room-1",
        memory_type=RoomMemoryType.ROOM_LORE,
        content="private room memory body",
        evidence_event_ids=["event-1"],
        confidence=0.9,
        revision=2,
        created_at_ms=10,
        updated_at_ms=20,
    )
    memory_slice = RoomMemorySlice(
        room_id="room-1",
        memory_revision=2,
        memory_ids=[memory.memory_id],
        items=[memory],
    )
    redacted = runtime_context()
    redacted.room_memory_slice = memory_slice
    request = ViewerRuntime(
        provider=PublishedProvider(),
        barrage_pipeline=AcceptingPipeline(),
        session_fence=AcceptingFence(),
        publisher=Sink(),
        room_service=Sink(),
        clock=MutableClock(),
        id_generator=SequenceIds(),
        max_in_flight=1,
        trace_recorder=DebugService(TraceStore()),
    )._build_request(
        viewer=viewer(),
        wave=wave(),
        runtime=redacted,
        sequence=1,
    )
    trace = build_viewer_request_trace(
        request=request,
        viewer=viewer(),
        wave=wave(),
        decision=decision("viewer-1"),
        available_viewer_ids=("viewer-1",),
        runtime=redacted,
        queued_at_ms=100,
        dispatched_at_ms=101,
        completed_at_ms=102,
        response_status=TraceResponseStatus.SILENCE,
        retry_count=0,
        accepted=True,
    )
    serialized = json.dumps(trace.model_dump(mode="json"))
    assert "private room memory body" not in serialized
    assert trace.memory.memory_ids == ["memory-sensitive"]
    with pytest.raises(UnsafeTraceArtifactError):
        assert_redacted_artifact(memory)


def test_observation_wave_trace_is_queryable_without_viewer_requests() -> None:
    store = TraceStore()
    runtime = build_runtime(
        PublishedProvider(),
        clock=MutableClock(),
        recorder=DebugService(store),
    )
    runtime.record_observation_trace(
        wave=wave(),
        runtime=runtime_context(),
        status=ObservationWaveStatus.EMPTY,
        decision=decision(),
    )

    result = store.query(TraceQuery(observation_id="observation-1"))
    assert result.items == []
    assert len(result.waves) == 1
    assert result.waves[0].director_status is ObservationWaveStatus.EMPTY
    assert result.waves[0].selected_viewer_ids == []
    assert result.waves[0].memory.memory_ids == ["memory-1"]
    assert_redacted_artifact(result)


def test_debug_artifact_exports_observation_wave_traces() -> None:
    store = TraceStore()
    recorder = DebugService(store)
    runtime = build_runtime(
        PublishedProvider(),
        clock=MutableClock(),
        recorder=recorder,
    )
    runtime.record_observation_trace(
        wave=wave(),
        runtime=runtime_context(),
        status=ObservationWaveStatus.EMPTY,
        decision=decision(),
    )

    artifact = recorder.export_artifact(
        TraceQuery(observation_id="observation-1")
    )

    assert artifact["items"] == []
    assert len(artifact["waves"]) == 1
    assert artifact["waves"][0]["trace_kind"] == "observation_wave"
    assert artifact["waves"][0]["memory"]["memory_ids"] == ["memory-1"]
    assert_redacted_artifact(artifact)


class BlockingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def generate(self, request: object) -> ViewerGenerationResponse:
        del request
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError


class CancellationSwallowingSilenceProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.SILENCE,
            reaction_type="silence",
        )


@pytest.mark.asyncio
async def test_session_stop_records_cancelled_request() -> None:
    store = TraceStore()
    provider = BlockingProvider()
    runtime = build_runtime(
        provider,
        clock=MutableClock(),
        recorder=DebugService(store),
    )
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch(
            wave=wave(),
            decision=decision("viewer-1"),
            pool=SimpleNamespace(viewers=(viewer(),)),
            runtime=runtime_context(),
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    await runtime.stop_session("session-1")
    summary = await asyncio.wait_for(dispatch, timeout=1)

    assert summary.cancelled == 1
    trace = store.query().items[0]
    assert trace.response_status is TraceResponseStatus.CANCELLED
    assert trace.stale_or_cancel_reason == "session_stopped"


@pytest.mark.asyncio
async def test_cancelled_silence_cannot_become_accepted_after_session_stop() -> None:
    store = TraceStore()
    provider = CancellationSwallowingSilenceProvider()
    runtime = build_runtime(
        provider,
        clock=MutableClock(),
        recorder=DebugService(store),
    )
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch(
            wave=wave(),
            decision=decision("viewer-1"),
            pool=SimpleNamespace(viewers=(viewer(),)),
            runtime=runtime_context(),
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    await runtime.stop_session("session-1")
    summary = await asyncio.wait_for(dispatch, timeout=1)

    assert summary.cancelled == 1
    assert summary.silenced == 0
    trace = store.query().items[0]
    assert trace.response_status is TraceResponseStatus.CANCELLED
    assert trace.validation.accepted is False
    assert trace.side_effects.published_barrage_id is None


class CompletionOrderProvider:
    def __init__(self) -> None:
        self.started = {
            "viewer-1": asyncio.Event(),
            "viewer-2": asyncio.Event(),
        }
        self.release = {
            "viewer-1": asyncio.Event(),
            "viewer-2": asyncio.Event(),
        }

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.started[request.viewer_instance_id].set()
        await self.release[request.viewer_instance_id].wait()
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.SILENCE,
            reaction_type="silence",
        )


@pytest.mark.asyncio
async def test_concurrent_traces_are_recorded_as_requests_complete() -> None:
    store = TraceStore()
    provider = CompletionOrderProvider()
    runtime = build_runtime(
        provider,
        clock=MutableClock(),
        recorder=DebugService(store),
    )
    await runtime.start_session("session-1")
    dispatch = asyncio.create_task(
        runtime.dispatch(
            wave=wave(),
            decision=decision("viewer-1", "viewer-2"),
            pool=SimpleNamespace(viewers=(viewer("viewer-1"), viewer("viewer-2"))),
            runtime=runtime_context(),
        )
    )
    await asyncio.gather(
        provider.started["viewer-1"].wait(),
        provider.started["viewer-2"].wait(),
    )
    provider.release["viewer-2"].set()
    for _ in range(20):
        if store.query().items:
            break
        await asyncio.sleep(0)
    provider.release["viewer-1"].set()
    await asyncio.wait_for(dispatch, timeout=1)

    assert [trace.viewer_instance_id for trace in store.query().items] == [
        "viewer-2",
        "viewer-1",
    ]
