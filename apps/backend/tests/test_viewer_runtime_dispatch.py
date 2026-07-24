import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ProviderRuntimeSpec,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.observation_wave import (
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.viewer import ViewerInstance, ViewerInstanceVariant


class MutableClock:
    def __init__(self, value: int = 100) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value


class SequenceIdGenerator:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"request-{self.value}"


def viewer(viewer_id: str, *, epoch: int = 1) -> ViewerInstance:
    return ViewerInstance(
        viewer_instance_id=viewer_id,
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        persona_id=f"persona-{viewer_id}",
        persona_revision=1,
        ordinal=1,
        display_name=viewer_id,
        variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="gameplay",
            silence_tendency=0.2,
        ),
        created_at_ms=0,
    )


def wave(
    *,
    observation_id: str = "wave-1",
    epoch: int = 1,
    target_viewer_id: str | None = None,
    target_persona_id: str | None = None,
) -> ObservationWave:
    return ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        observation_id=observation_id,
        created_at_ms=100,
        deadline_at_ms=1_000,
        triggers=[ObservationTrigger.USER_TEXT],
        event_ids=["event-1"],
        trigger_event_ids=["event-1"],
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="Text-only user input.",
        target_viewer_id=target_viewer_id,
        target_persona_id=target_persona_id,
    )


def decision(*viewer_ids: str, observation_id: str = "wave-1", epoch: int = 1):
    return CrowdDecision(
        decision_id=f"decision-{observation_id}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        observation_id=observation_id,
        selected_viewer_ids=list(viewer_ids),
        evidence_event_ids=["event-1"],
        created_at_ms=100,
        expires_at_ms=1_000,
    )


class RecordingDirectorProvider:
    def __init__(self, selected_ids: tuple[str, ...]) -> None:
        self.selected_ids = selected_ids
        self.calls: list[object] = []

    async def decide(self, request: object) -> CrowdDecision:
        self.calls.append(request)
        return decision(*self.selected_ids)


class FixedBudget:
    def maximum(self, **_: object) -> int:
        return 3


class NoFallback:
    def decide(self, **_: object) -> CrowdDecision:
        raise AssertionError("fallback must not run")


def test_dispatch_summary_silence_is_an_instance_property() -> None:
    from advx_backend.application.viewer_runtime import ViewerDispatchSummary

    assert isinstance(ViewerDispatchSummary.__dict__["silence"], property)
    assert ViewerDispatchSummary(silenced=2).silence == 2


@pytest.mark.asyncio
async def test_director_is_called_exactly_once_for_each_wave() -> None:
    from advx_backend.application.director_service import DirectorService

    provider = RecordingDirectorProvider(("viewer-1",))
    service = DirectorService(
        provider=provider,
        budget_policy=FixedBudget(),
        fallback=NoFallback(),
        clock=MutableClock(),
    )

    outcome = await service.decide(
        wave=wave(),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert len(provider.calls) == 1
    assert outcome.decision.selected_viewer_ids == ["viewer-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_values", "selected_ids"),
    [
        ({"target_viewer_id": "viewer-1"}, ()),
        ({"target_persona_id": "persona-viewer-1"}, ("viewer-2",)),
    ],
)
async def test_director_cannot_omit_a_structured_target(
    target_values: dict[str, str],
    selected_ids: tuple[str, ...],
) -> None:
    from advx_backend.application.director_service import (
        DirectorDecisionError,
        DirectorService,
    )

    service = DirectorService(
        provider=RecordingDirectorProvider(selected_ids),
        budget_policy=FixedBudget(),
        fallback=NoFallback(),
        clock=MutableClock(),
    )

    with pytest.raises(DirectorDecisionError, match="explicitly targeted"):
        await service.decide(
            wave=wave(**target_values),
            pool=SimpleNamespace(
                viewers=(viewer("viewer-1"), viewer("viewer-2"))
            ),
            runtime=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_targeted_viewer_may_be_selected_and_explicitly_choose_silence() -> None:
    from advx_backend.application.director_service import DirectorService

    provider = RecordingDirectorProvider(("viewer-1",))
    service = DirectorService(
        provider=provider,
        budget_policy=FixedBudget(),
        fallback=NoFallback(),
        clock=MutableClock(),
    )

    outcome = await service.decide(
        wave=wave(target_viewer_id="viewer-1"),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert outcome.decision.selected_viewer_ids == ["viewer-1"]
    request = provider.calls[0]
    assert request.forced_viewer_ids == ("viewer-1",)


@pytest.mark.asyncio
async def test_resilient_mode_falls_back_after_invalid_provider_decision() -> None:
    from advx_backend.application.director_service import DirectorService

    class Fallback:
        def decide(self, **_: object) -> CrowdDecision:
            return decision("viewer-1")

    service = DirectorService(
        provider=RecordingDirectorProvider(("unknown-viewer",)),
        budget_policy=FixedBudget(),
        fallback=Fallback(),
        clock=MutableClock(),
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(director_failure_mode="resilient")
    )

    outcome = await service.decide(
        wave=wave(),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=runtime,
    )

    assert outcome.decision.selected_viewer_ids == ["viewer-1"]
    assert outcome.decision.decision_source.value == "fallback"


@pytest.mark.asyncio
async def test_resilient_mode_still_rejects_an_invalid_fallback() -> None:
    from advx_backend.application.director_service import (
        DirectorDecisionError,
        DirectorService,
    )

    class InvalidFallback:
        def decide(self, **_: object) -> CrowdDecision:
            return decision("unknown-viewer")

    service = DirectorService(
        provider=RecordingDirectorProvider(("unknown-viewer",)),
        budget_policy=FixedBudget(),
        fallback=InvalidFallback(),
        clock=MutableClock(),
    )
    runtime = SimpleNamespace(
        settings=SimpleNamespace(director_failure_mode="resilient")
    )

    with pytest.raises(DirectorDecisionError, match="unknown Viewer"):
        await service.decide(
            wave=wave(),
            pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
            runtime=runtime,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_update", "message"),
    [
        ({"namespace_id": "mode-other"}, "active mode"),
        ({"observation_id": "wave-other"}, "scope does not match"),
        ({"evidence_event_ids": ["event-other"]}, "unknown event"),
        ({"evidence_frame_indexes": [0]}, "unknown frame"),
    ],
)
async def test_director_rejects_out_of_scope_meme_candidate_from_adapter(
    candidate_update: dict[str, object],
    message: str,
) -> None:
    from advx_backend.application.director_service import (
        DirectorDecisionError,
        DirectorOutcome,
        DirectorService,
    )

    candidate = MemeCandidate(
        candidate_id="candidate-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="wave-1",
        namespace_id="mode-1",
        text="candidate",
        evidence_event_ids=["event-1"],
        created_at_ms=100,
    ).model_copy(update=candidate_update)

    class CandidateProvider:
        async def decide(self, _request: object) -> DirectorOutcome:
            return DirectorOutcome(
                decision=decision(),
                meme_candidate=candidate,
            )

    service = DirectorService(
        provider=CandidateProvider(),
        budget_policy=FixedBudget(),
        fallback=NoFallback(),
        clock=MutableClock(),
    )

    with pytest.raises(DirectorDecisionError, match=message):
        await service.decide(
            wave=wave(),
            pool=SimpleNamespace(viewers=()),
            runtime=SimpleNamespace(
                active_mode_id="mode-1",
                modes=(
                    SimpleNamespace(
                        mode_id="mode-1",
                        namespace_id="mode-1",
                    ),
                ),
                settings=SimpleNamespace(director_failure_mode="resilient"),
            ),
        )


class RecordingViewerProvider:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            text=f"from {request.viewer_instance_id}",
            reaction_type="reply",
            evidence_refs=[
                EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")
            ],
        )


class AcceptingPipeline:
    def validate(self, *, request: object, response: object) -> object:
        return SimpleNamespace(accepted=True, event=response, rejection_reason=None)


class Fence:
    def __init__(self, accepting_epoch: int = 1) -> None:
        self.accepting_epoch = accepting_epoch

    async def accepts(self, **scope: object) -> bool:
        return scope["audience_epoch"] == self.accepting_epoch


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class FailingPublisher(RecordingPublisher):
    async def publish(self, event: object) -> None:
        del event
        raise ConnectionError("realtime unavailable")


class RecordingRoom:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append_published_barrage(self, event: object) -> None:
        self.events.append(event)


def runtime(
    provider: RecordingViewerProvider,
    *,
    fence: Fence | None = None,
    pipeline: AcceptingPipeline | None = None,
    max_in_flight: int = 12,
) -> tuple[object, RecordingPublisher, RecordingRoom]:
    from advx_backend.application.viewer_runtime import ViewerRuntime

    publisher = RecordingPublisher()
    room = RecordingRoom()
    instance = ViewerRuntime(
        provider=provider,
        barrage_pipeline=pipeline or AcceptingPipeline(),
        session_fence=fence or Fence(),
        publisher=publisher,
        room_service=room,
        clock=MutableClock(),
        id_generator=SequenceIdGenerator(),
        max_in_flight=max_in_flight,
    )
    return instance, publisher, room


def runtime_context(
    *,
    max_in_flight: int,
    queue_capacity: int,
    revision: int,
) -> object:
    return SimpleNamespace(
        canonical_runtime_spec=SimpleNamespace(
            config_revision=revision,
            settings=SimpleNamespace(
                max_in_flight_viewer_requests=max_in_flight,
                viewer_queue_capacity=queue_capacity,
            ),
        )
    )


@pytest.mark.asyncio
async def test_selected_viewers_use_independent_provider_requests() -> None:
    provider = RecordingViewerProvider()
    instance, publisher, _ = runtime(provider)
    await instance.start_session("session-1")
    viewers = (viewer("viewer-1"), viewer("viewer-2"), viewer("viewer-3"))

    summary = await instance.dispatch(
        wave=wave(),
        decision=decision(*(item.viewer_instance_id for item in viewers)),
        pool=SimpleNamespace(viewers=viewers),
        runtime=SimpleNamespace(),
    )

    assert len(provider.requests) == 3
    assert {request.viewer_instance_id for request in provider.requests} == {
        "viewer-1",
        "viewer-2",
        "viewer-3",
    }
    assert all(
        len({request.viewer_instance_id}) == 1 for request in provider.requests
    )
    assert len(publisher.events) == 3
    assert summary.selected == 3
    assert summary.queued == 0
    assert summary.dispatched == 3
    assert summary.completed == 3
    assert summary.published == 3


class RetryOnceProvider(RecordingViewerProvider):
    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise ConnectionError("temporary network failure")
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            text="recovered",
            reaction_type="reply",
            evidence_refs=[
                EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")
            ],
        )


class DuplicateOutputProvider(RecordingViewerProvider):
    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        text = "Nice play!" if request.viewer_instance_id == "viewer-1" else "  nice   play "
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.BARRAGE,
            text=text,
            reaction_type="reply",
            evidence_refs=[
                EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")
            ],
        )


@pytest.mark.asyncio
async def test_wave_semantic_duplicate_is_claimed_before_any_side_effect() -> None:
    provider = DuplicateOutputProvider()
    instance, publisher, room = runtime(provider, max_in_flight=2)
    await instance.start_session("session-1")
    viewers = (viewer("viewer-1"), viewer("viewer-2"))

    summary = await instance.dispatch(
        wave=wave(),
        decision=decision("viewer-1", "viewer-2"),
        pool=SimpleNamespace(viewers=viewers),
        runtime=SimpleNamespace(),
    )

    assert len(provider.requests) == 2
    assert summary.published == 1
    assert summary.rejected == 1
    assert len(publisher.events) == 1
    assert len(room.events) == 1


@pytest.mark.asyncio
async def test_session_stop_clears_wave_semantic_dedup_state() -> None:
    provider = DuplicateOutputProvider()
    instance, publisher, room = runtime(provider)
    await instance.start_session("session-1")
    pool = SimpleNamespace(viewers=(viewer("viewer-1"),))

    first = await instance.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=pool,
        runtime=SimpleNamespace(),
    )
    await instance.stop_session("session-1")
    await instance.start_session("session-1")
    restarted = await instance.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=pool,
        runtime=SimpleNamespace(),
    )

    assert first.published == 1
    assert restarted.published == 1
    assert len(publisher.events) == 2
    assert len(room.events) == 2


@pytest.mark.asyncio
async def test_transient_failure_retries_the_same_viewer_once() -> None:
    provider = RetryOnceProvider()
    instance, publisher, _ = runtime(provider)
    await instance.start_session("session-1")

    summary = await instance.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert [request.viewer_instance_id for request in provider.requests] == [
        "viewer-1",
        "viewer-1",
    ]
    assert len(publisher.events) == 1
    assert summary.retry == 1
    assert summary.dispatched == 1
    assert summary.completed == 1


@pytest.mark.asyncio
async def test_direct_frames_without_a_frame_bundle_are_explicitly_rejected() -> None:
    provider = RecordingViewerProvider()
    instance, publisher, room = runtime(provider)
    await instance.start_session("session-1")
    frame_less_wave = wave().model_copy(
        update={
            "visual_input_mode": ViewerVisualInputMode.DIRECT_FRAMES,
            "shared_visual_summary": None,
        }
    )

    summary = await instance.dispatch(
        wave=frame_less_wave,
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert summary.selected == 1
    assert summary.rejected == 1
    assert summary.dispatched == 0
    assert provider.requests == []
    assert publisher.events == []
    assert room.events == []


@pytest.mark.asyncio
async def test_realtime_failure_keeps_the_durable_publish_outcome() -> None:
    from advx_backend.application.viewer_runtime import ViewerRuntime

    provider = RecordingViewerProvider()
    publisher = FailingPublisher()
    room = RecordingRoom()
    instance = ViewerRuntime(
        provider=provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=Fence(),
        publisher=publisher,
        room_service=room,
        clock=MutableClock(),
        id_generator=SequenceIdGenerator(),
        max_in_flight=1,
    )
    await instance.start_session("session-1")

    summary = await instance.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert summary.published == 1
    assert len(room.events) == 1


class GatedViewerProvider(RecordingViewerProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.SILENCE,
            reaction_type="silence",
        )


@pytest.mark.asyncio
async def test_global_queue_never_exceeds_configured_in_flight_limit() -> None:
    provider = GatedViewerProvider()
    instance, _, _ = runtime(provider, max_in_flight=1)
    await instance.start_session("session-1")
    viewers = (viewer("viewer-1"), viewer("viewer-2"))
    dispatched = asyncio.create_task(
        instance.dispatch(
            wave=wave(),
            decision=decision("viewer-1", "viewer-2"),
            pool=SimpleNamespace(viewers=viewers),
            runtime=SimpleNamespace(),
        )
    )

    await asyncio.wait_for(provider.started.wait(), timeout=1)
    await asyncio.sleep(0)

    assert len(provider.requests) == 1
    assert provider.max_active == 1

    provider.release.set()
    await asyncio.wait_for(dispatched, timeout=1)
    assert len(provider.requests) == 2


class EpochGatedViewerProvider(RecordingViewerProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release_by_epoch = {1: asyncio.Event(), 2: asyncio.Event()}
        self.active_by_epoch = {1: 0, 2: 0}
        self.max_active_by_epoch = {1: 0, 2: 0}

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        epoch = request.audience_epoch
        self.active_by_epoch[epoch] += 1
        self.max_active_by_epoch[epoch] = max(
            self.max_active_by_epoch[epoch],
            self.active_by_epoch[epoch],
        )
        try:
            await self.release_by_epoch[epoch].wait()
        finally:
            self.active_by_epoch[epoch] -= 1
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.SILENCE,
            reaction_type="silence",
        )


async def wait_for_request_count(
    provider: RecordingViewerProvider,
    *,
    epoch: int,
    count: int,
) -> None:
    async def reached_count() -> None:
        while (
            len(
                [
                    request
                    for request in provider.requests
                    if request.audience_epoch == epoch
                ]
            )
            < count
        ):
            await asyncio.sleep(0)

    await asyncio.wait_for(reached_count(), timeout=1)


@pytest.mark.asyncio
async def test_committed_runtime_limits_hot_update_between_epochs() -> None:
    provider = EpochGatedViewerProvider()
    instance, _, _ = runtime(provider, max_in_flight=12)
    await instance.start_session("session-1")

    epoch_one = asyncio.create_task(
        instance.dispatch(
            wave=wave(epoch=1),
            decision=decision("viewer-1", "viewer-2", epoch=1),
            pool=SimpleNamespace(
                viewers=(viewer("viewer-1", epoch=1), viewer("viewer-2", epoch=1))
            ),
            runtime=runtime_context(max_in_flight=1, queue_capacity=2, revision=1),
        )
    )
    await wait_for_request_count(provider, epoch=1, count=1)
    await asyncio.sleep(0)
    assert provider.max_active_by_epoch[1] == 1
    provider.release_by_epoch[1].set()
    await asyncio.wait_for(epoch_one, timeout=1)

    epoch_two = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-2", epoch=2),
            decision=decision(
                "viewer-1",
                "viewer-2",
                observation_id="wave-2",
                epoch=2,
            ),
            pool=SimpleNamespace(
                viewers=(viewer("viewer-1", epoch=2), viewer("viewer-2", epoch=2))
            ),
            runtime=runtime_context(max_in_flight=2, queue_capacity=2, revision=2),
        )
    )
    await wait_for_request_count(provider, epoch=2, count=2)
    assert provider.max_active_by_epoch[2] == 2
    provider.release_by_epoch[2].set()
    await asyncio.wait_for(epoch_two, timeout=1)


@pytest.mark.asyncio
async def test_committed_runtime_queue_capacity_cancels_overflow() -> None:
    provider = GatedViewerProvider()
    instance, publisher, room = runtime(provider, max_in_flight=12)
    await instance.start_session("session-1")
    viewers = tuple(viewer(f"viewer-{index}") for index in range(1, 4))
    dispatched = asyncio.create_task(
        instance.dispatch(
            wave=wave(),
            decision=decision(*(item.viewer_instance_id for item in viewers)),
            pool=SimpleNamespace(viewers=viewers),
            runtime=runtime_context(max_in_flight=1, queue_capacity=1, revision=1),
        )
    )

    await asyncio.wait_for(provider.started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert len(provider.requests) == 1

    provider.release.set()
    summary = await asyncio.wait_for(dispatched, timeout=1)
    assert len(provider.requests) == 2
    assert summary.selected == 3
    assert summary.queued == 1
    assert summary.dispatched == 2
    assert summary.completed == 2
    assert summary.cancelled == 1
    assert publisher.events == []
    assert room.events == []


@pytest.mark.asyncio
async def test_queued_request_expires_from_wave_creation_not_dispatch_time() -> None:
    from advx_backend.application.viewer_runtime import ViewerRuntime

    provider = GatedViewerProvider()
    clock = MutableClock(100)
    publisher = RecordingPublisher()
    room = RecordingRoom()
    instance = ViewerRuntime(
        provider=provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=Fence(),
        publisher=publisher,
        room_service=room,
        clock=clock,
        id_generator=SequenceIdGenerator(),
        max_in_flight=1,
    )
    await instance.start_session("session-1")
    expiring_wave = wave()
    viewers = (viewer("viewer-1"), viewer("viewer-2"))
    dispatched = asyncio.create_task(
        instance.dispatch(
            wave=expiring_wave,
            decision=decision("viewer-1", "viewer-2"),
            pool=SimpleNamespace(viewers=viewers),
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    clock.value = expiring_wave.deadline_at_ms
    provider.release.set()
    summary = await asyncio.wait_for(dispatched, timeout=1)

    assert len(provider.requests) == 1
    assert summary.selected == 2
    assert summary.queued == 1
    assert summary.dispatched == 1
    assert summary.completed == 1
    assert summary.expired == 2
    assert summary.silenced == 0
    assert publisher.events == []
    assert room.events == []


class FirstRequestBlockingProvider(RecordingViewerProvider):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_started.set()
            await self.release_first.wait()
        return ViewerGenerationResponse(
            generation_request_id=request.generation_request_id,
            viewer_instance_id=request.viewer_instance_id,
            viewer_sequence=request.viewer_sequence,
            action=ViewerAction.SILENCE,
            reaction_type="silence",
        )


@pytest.mark.asyncio
async def test_latest_wave_replaces_an_older_pending_request_for_the_same_viewer() -> None:
    provider = FirstRequestBlockingProvider()
    instance, _, _ = runtime(provider, max_in_flight=1)
    await instance.start_session("session-1")
    pool = SimpleNamespace(viewers=(viewer("viewer-1"),))

    first = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-1"),
            decision=decision("viewer-1", observation_id="wave-1"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=1)
    replaced = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-2"),
            decision=decision("viewer-1", observation_id="wave-2"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    latest = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-3"),
            decision=decision("viewer-1", observation_id="wave-3"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )

    provider.release_first.set()
    await asyncio.wait_for(asyncio.gather(first, replaced, latest), timeout=1)

    assert [request.observation_id for request in provider.requests] == [
        "wave-1",
        "wave-3",
    ]


@pytest.mark.asyncio
async def test_latest_wave_replaces_a_queued_viewer_before_capacity_rejection() -> None:
    provider = FirstRequestBlockingProvider()
    instance, _, _ = runtime(provider, max_in_flight=1)
    await instance.start_session("session-1")
    pool = SimpleNamespace(viewers=(viewer("viewer-1"), viewer("viewer-2")))
    limits = runtime_context(max_in_flight=1, queue_capacity=1, revision=1)

    active = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-1"),
            decision=decision("viewer-1", observation_id="wave-1"),
            pool=pool,
            runtime=limits,
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=1)
    queued = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-2"),
            decision=decision("viewer-2", observation_id="wave-2"),
            pool=pool,
            runtime=limits,
        )
    )
    await asyncio.sleep(0)
    latest = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-3"),
            decision=decision("viewer-2", observation_id="wave-3"),
            pool=pool,
            runtime=limits,
        )
    )
    await asyncio.sleep(0)

    provider.release_first.set()
    active_summary, queued_summary, latest_summary = await asyncio.wait_for(
        asyncio.gather(active, queued, latest),
        timeout=1,
    )

    assert [request.observation_id for request in provider.requests] == [
        "wave-1",
        "wave-3",
    ]
    assert active_summary.silenced == 1
    assert queued_summary.superseded == 1
    assert queued_summary.cancelled == 0
    assert latest_summary.silenced == 1
    assert latest_summary.queued == 1


@pytest.mark.asyncio
async def test_latest_queued_viewer_hands_off_across_runtime_lanes() -> None:
    provider = FirstRequestBlockingProvider()
    instance, _, _ = runtime(provider, max_in_flight=1)
    await instance.start_session("session-1")
    pool = SimpleNamespace(viewers=(viewer("viewer-1"), viewer("viewer-2")))

    active = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-1"),
            decision=decision("viewer-1", observation_id="wave-1"),
            pool=pool,
            runtime=runtime_context(max_in_flight=1, queue_capacity=1, revision=1),
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=1)
    queued = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-2"),
            decision=decision("viewer-2", observation_id="wave-2"),
            pool=pool,
            runtime=runtime_context(max_in_flight=1, queue_capacity=1, revision=1),
        )
    )
    await asyncio.sleep(0)
    latest = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-3"),
            decision=decision("viewer-2", observation_id="wave-3"),
            pool=pool,
            runtime=runtime_context(max_in_flight=1, queue_capacity=1, revision=2),
        )
    )

    await wait_for_request_count(provider, epoch=1, count=2)
    provider.release_first.set()
    _, queued_summary, latest_summary = await asyncio.wait_for(
        asyncio.gather(active, queued, latest),
        timeout=1,
    )

    assert [request.observation_id for request in provider.requests] == [
        "wave-1",
        "wave-3",
    ]
    assert queued_summary.superseded == 1
    assert latest_summary.silenced == 1
    assert latest_summary.queued == 1


@pytest.mark.asyncio
async def test_real_openai_adapter_retries_viewer_429_then_succeeds() -> None:
    from advx_backend.providers.model.viewer_runtime import (
        OpenAICompatibleViewerRuntimeConfig,
        OpenAICompatibleViewerRuntimeProvider,
    )

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, request=request)
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        output = {
            "generation_request_id": context["generation_request_id"],
            "viewer_instance_id": context["viewer_instance_id"],
            "viewer_sequence": context["viewer_sequence"],
            "action": "silence",
            "text": None,
            "reaction_type": "none",
            "evidence_refs": [],
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {"message": {"content": json.dumps(output)}},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleViewerRuntimeProvider(
        OpenAICompatibleViewerRuntimeConfig(
            base_url="https://models.example/v1",
            provider=ProviderRuntimeSpec(
                provider_profile_id="profile-1",
                director_model="director-model",
                viewer_model="viewer-model",
                memory_model="memory-model",
                visual_summary_model="visual-model",
            ),
            api_key="secret",
        ),
        client=client,
    )
    instance, _, _ = runtime(provider)
    await instance.start_session("session-1")

    summary = await instance.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert attempts == 2
    assert summary.silenced == 1
    assert summary.retry == 1

    await provider.aclose()
    await client.aclose()


class NeverReturningProvider(RecordingViewerProvider):
    async def generate(self, request: object) -> ViewerGenerationResponse:
        self.requests.append(request)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_each_provider_attempt_is_timeboxed_by_remaining_ttl() -> None:
    provider = NeverReturningProvider()
    instance, publisher, room = runtime(provider, max_in_flight=1)
    await instance.start_session("session-1")
    short_wave = wave().model_copy(update={"deadline_at_ms": 150})

    summary = await asyncio.wait_for(
        instance.dispatch(
            wave=short_wave,
            decision=decision("viewer-1"),
            pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
            runtime=SimpleNamespace(),
        ),
        timeout=0.5,
    )

    assert len(provider.requests) == 1
    assert summary.expired == 1
    assert summary.failed == 0
    assert summary.dispatched == 1
    assert summary.completed == 1
    assert publisher.events == []
    assert room.events == []


@pytest.mark.asyncio
async def test_retry_requires_backoff_and_a_minimum_second_attempt_budget() -> None:
    provider = RetryOnceProvider()
    instance, publisher, room = runtime(provider, max_in_flight=1)
    await instance.start_session("session-1")
    short_wave = wave().model_copy(update={"deadline_at_ms": 150})

    summary = await instance.dispatch(
        wave=short_wave,
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert len(provider.requests) == 1
    assert summary.expired == 1
    assert summary.retry == 0
    assert publisher.events == []
    assert room.events == []


@pytest.mark.asyncio
async def test_session_stop_resolves_active_and_pending_mailbox_requests() -> None:
    provider = FirstRequestBlockingProvider()
    instance, publisher, room = runtime(provider, max_in_flight=1)
    await instance.start_session("session-1")
    pool = SimpleNamespace(viewers=(viewer("viewer-1"),))
    active = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-1"),
            decision=decision("viewer-1", observation_id="wave-1"),
            pool=pool,
            runtime=runtime_context(max_in_flight=1, queue_capacity=1, revision=1),
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=1)
    pending = asyncio.create_task(
        instance.dispatch(
            wave=wave(observation_id="wave-2"),
            decision=decision("viewer-1", observation_id="wave-2"),
            pool=pool,
            runtime=runtime_context(max_in_flight=1, queue_capacity=1, revision=1),
        )
    )
    await asyncio.sleep(0)

    await instance.stop_session("session-1")
    active_summary, pending_summary = await asyncio.wait_for(
        asyncio.gather(active, pending),
        timeout=1,
    )

    assert active_summary.cancelled == 1
    assert pending_summary.cancelled == 1
    assert publisher.events == []
    assert room.events == []


@pytest.mark.asyncio
async def test_old_epoch_result_has_zero_publish_or_room_side_effects() -> None:
    provider = RecordingViewerProvider()
    instance, publisher, room = runtime(provider, fence=Fence(accepting_epoch=2))
    await instance.start_session("session-1")

    summary = await instance.dispatch(
        wave=wave(epoch=1),
        decision=decision("viewer-1", epoch=1),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert summary.published == 0
    assert publisher.events == []
    assert room.events == []


class RejectingEvidencePipeline(AcceptingPipeline):
    def validate(self, *, request: object, response: object) -> object:
        del request, response
        return SimpleNamespace(
            accepted=False,
            event=None,
            rejection_reason="evidence_not_in_request",
        )


@pytest.mark.asyncio
async def test_invalid_evidence_has_zero_publish_or_room_side_effects() -> None:
    provider = RecordingViewerProvider()
    instance, publisher, room = runtime(provider, pipeline=RejectingEvidencePipeline())
    await instance.start_session("session-1")

    summary = await instance.dispatch(
        wave=wave(),
        decision=decision("viewer-1"),
        pool=SimpleNamespace(viewers=(viewer("viewer-1"),)),
        runtime=SimpleNamespace(),
    )

    assert summary.rejected == 1
    assert publisher.events == []
    assert room.events == []
