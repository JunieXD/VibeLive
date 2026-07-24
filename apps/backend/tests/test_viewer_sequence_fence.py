import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest

from advx_backend.application.director_service import DirectorOutcome
from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.viewer_pool_service import ViewerPoolSnapshot
from advx_backend.application.viewer_runtime import ViewerDispatchSummary, ViewerRuntime
from advx_backend.application.viewer_runtime_coordinator import ViewerRuntimeCoordinator
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import (
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.viewer import ViewerInstance, ViewerInstanceVariant


class FixedClock:
    def now_ms(self) -> int:
        return 100


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
        return f"id-{self.value}"


class BlockingFirstProvider:
    def __init__(self) -> None:
        self.requests: list[object] = []
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
            action=ViewerAction.BARRAGE,
            text=request.observation_id,
            reaction_type="reply",
            evidence_refs=[
                EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")
            ],
        )


class BlockingFirstSilenceProvider(BlockingFirstProvider):
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


class AcceptingPipeline:
    def validate(self, *, request: object, response: object) -> object:
        del request
        return SimpleNamespace(accepted=True, event=response, rejection_reason=None)


class Recorder:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)

    async def append_published_barrage(self, event: object) -> None:
        self.events.append(event)


class BlockingPublisher(Recorder):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def publish(self, event: object) -> None:
        self.entered.set()
        await self.release.wait()
        self.events.append(event)


def viewer(*, epoch: int = 1, revision: int = 1) -> ViewerInstance:
    return ViewerInstance(
        viewer_instance_id="viewer-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=epoch,
        persona_id="persona-1",
        persona_revision=revision,
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
        created_at_ms=0,
    )


def runtime_spec(*, persona_marker: str = "stable") -> object:
    persona = SimpleNamespace(persona_id="persona-1", marker=persona_marker)
    mode = SimpleNamespace(
        mode_id="mode-1",
        namespace_id="mode-1",
        persona_overrides={},
    )
    return SimpleNamespace(
        room=SimpleNamespace(room_id="room-1"),
        active_mode_id="mode-1",
        personas=[persona],
        modes=[mode],
    )


def state(
    *,
    epoch: int = 1,
    persona_marker: str = "stable",
    include_viewer: bool = True,
) -> CommittedRuntime:
    viewers = [viewer(epoch=epoch)] if include_viewer else []
    return CommittedRuntime(
        session_id="session-1",
        spec=runtime_spec(persona_marker=persona_marker),
        audience_epoch=epoch,
        pool=ViewerPoolSnapshot(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=epoch,
            mode_id="mode-1",
            session_seed="seed",
            viewers=viewers,
        ),
    )


def wave(observation_id: str) -> ObservationWave:
    return ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=observation_id,
        created_at_ms=100,
        deadline_at_ms=1_000,
        triggers=[ObservationTrigger.USER_TEXT],
        event_ids=["event-1"],
        trigger_event_ids=["event-1"],
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="Text-only user input.",
    )


def decision(observation_id: str) -> CrowdDecision:
    return CrowdDecision(
        decision_id=f"decision-{observation_id}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id=observation_id,
        selected_viewer_ids=["viewer-1"],
        evidence_event_ids=["event-1"],
        created_at_ms=100,
        expires_at_ms=1_000,
    )


class FixedWaveCoordinator(ViewerRuntimeCoordinator):
    async def _build_wave(self, observation: object, committed: object) -> ObservationWave:
        del observation, committed
        return wave("wave-effects")

    async def _read_memory_slice(
        self,
        wave: ObservationWave,
    ) -> RoomMemorySlice:
        return RoomMemorySlice(room_id=wave.room_id, memory_revision=0)

    @staticmethod
    def _freeze_runtime(
        spec: object,
        observation: object,
        memory_slice: RoomMemorySlice,
    ) -> object:
        del observation, memory_slice
        return SimpleNamespace(canonical_runtime_spec=spec)

    async def _prepare_visual_wave(
        self,
        wave: ObservationWave,
        runtime: object,
    ) -> ObservationWave:
        del runtime
        return wave


class MemeDirector:
    def __init__(self, *, selected: bool = True) -> None:
        self.selected = selected

    async def decide(self, *, wave: ObservationWave, **_: object) -> DirectorOutcome:
        selected = decision(wave.observation_id)
        if not self.selected:
            selected = selected.model_copy(update={"selected_viewer_ids": []})
        return DirectorOutcome(
            decision=selected,
            meme_candidate=MemeCandidate(
                candidate_id="candidate-1",
                room_id=wave.room_id,
                session_id=wave.session_id,
                audience_epoch=wave.audience_epoch,
                observation_id=wave.observation_id,
                namespace_id="mode-1",
                text="candidate",
                evidence_event_ids=["event-1"],
                created_at_ms=wave.created_at_ms,
            ),
        )


class FixedDispatchRuntime:
    def __init__(self, summary: ViewerDispatchSummary) -> None:
        self.summary = summary

    async def dispatch(self, **_: object) -> ViewerDispatchSummary:
        return self.summary


class StateChangingDispatchRuntime(FixedDispatchRuntime):
    def __init__(
        self,
        store: RuntimeStateStore,
        *,
        replacement: CommittedRuntime | None,
    ) -> None:
        super().__init__(ViewerDispatchSummary(silenced=1))
        self.store = store
        self.replacement = replacement

    async def dispatch(self, **_: object) -> ViewerDispatchSummary:
        if self.replacement is None:
            await self.store.stop("session-1")
        else:
            await self.store.replace(self.replacement)
        return self.summary


class RecordingMemeSink:
    def __init__(self) -> None:
        self.candidates: list[MemeCandidate] = []

    async def commit_candidate(self, candidate: MemeCandidate) -> object:
        self.candidates.append(candidate)
        return object()


class RecordingMemorySink:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def extract_after_wave(self, **values: object) -> None:
        self.calls.append(values)


class BlockingFencedMemorySink(RecordingMemorySink):
    def __init__(self) -> None:
        super().__init__()
        self.extraction_started = asyncio.Event()
        self.release_extraction = asyncio.Event()

    async def extract_after_wave_fenced(
        self,
        *,
        commit_effect: Callable[
            [Callable[[], Awaitable[object]]],
            Awaitable[tuple[bool, object | None]],
        ],
        **_: object,
    ) -> None:
        self.extraction_started.set()
        await self.release_extraction.wait()

        async def commit() -> object:
            self.calls.append("committed")
            return object()

        await commit_effect(commit)


async def run_wave_side_effects(
    *,
    summary: ViewerDispatchSummary,
    selected: bool = True,
    clock: MutableClock | None = None,
) -> tuple[RecordingMemeSink, RecordingMemorySink]:
    store = RuntimeStateStore()
    await store.activate(state())
    meme_sink = RecordingMemeSink()
    memory_sink = RecordingMemorySink()
    coordinator = FixedWaveCoordinator(
        runtime_state=store,
        director=MemeDirector(selected=selected),
        viewer_runtime=FixedDispatchRuntime(summary),
        meme_sink=meme_sink,
        memory_extraction_sink=memory_sink,
        clock=clock or MutableClock(),
    )

    await coordinator.react(SimpleNamespace(session_id="session-1"))
    await coordinator.wait_for_background_tasks()
    return meme_sink, memory_sink


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summary",
    [
        ViewerDispatchSummary(expired=1),
        ViewerDispatchSummary(cancelled=1),
    ],
    ids=["expired", "cancelled"],
)
async def test_non_accepted_dispatch_has_zero_memory_or_meme_side_effects(
    summary: ViewerDispatchSummary,
) -> None:
    meme_sink, memory_sink = await run_wave_side_effects(summary=summary)

    assert meme_sink.candidates == []
    assert memory_sink.calls == []


@pytest.mark.asyncio
async def test_expired_zero_viewer_wave_has_zero_memory_or_meme_side_effects() -> None:
    clock = MutableClock(1_000)

    meme_sink, memory_sink = await run_wave_side_effects(
        summary=ViewerDispatchSummary(),
        selected=False,
        clock=clock,
    )

    assert meme_sink.candidates == []
    assert memory_sink.calls == []


@pytest.mark.asyncio
async def test_valid_zero_viewer_wave_can_commit_memory_and_meme_side_effects() -> None:
    meme_sink, memory_sink = await run_wave_side_effects(
        summary=ViewerDispatchSummary(),
        selected=False,
    )

    assert [candidate.candidate_id for candidate in meme_sink.candidates] == [
        "candidate-1"
    ]
    assert len(memory_sink.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement",
    [None, state(epoch=2)],
    ids=["stopped-session", "replaced-epoch"],
)
async def test_final_wave_fence_blocks_memory_and_meme_after_state_changes(
    replacement: CommittedRuntime | None,
) -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    meme_sink = RecordingMemeSink()
    memory_sink = RecordingMemorySink()
    coordinator = FixedWaveCoordinator(
        runtime_state=store,
        director=MemeDirector(),
        viewer_runtime=StateChangingDispatchRuntime(
            store,
            replacement=replacement,
        ),
        meme_sink=meme_sink,
        memory_extraction_sink=memory_sink,
        clock=MutableClock(),
    )

    await coordinator.react(SimpleNamespace(session_id="session-1"))
    await coordinator.wait_for_background_tasks()

    assert meme_sink.candidates == []
    assert memory_sink.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transition",
    ["stop", "replace", "deadline"],
)
async def test_memory_provider_runs_outside_fence_and_commit_rechecks_wave(
    transition: str,
) -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    clock = MutableClock()
    memory_sink = BlockingFencedMemorySink()
    coordinator = FixedWaveCoordinator(
        runtime_state=store,
        director=MemeDirector(),
        viewer_runtime=FixedDispatchRuntime(ViewerDispatchSummary(silenced=1)),
        memory_extraction_sink=memory_sink,
        clock=clock,
    )

    await coordinator.react(SimpleNamespace(session_id="session-1"))
    await asyncio.wait_for(memory_sink.extraction_started.wait(), timeout=1)
    if transition == "stop":
        await asyncio.wait_for(store.stop("session-1"), timeout=1)
    elif transition == "replace":
        await asyncio.wait_for(store.replace(state(epoch=2)), timeout=1)
    else:
        clock.value = 1_000

    memory_sink.release_extraction.set()
    await coordinator.wait_for_background_tasks()

    assert memory_sink.calls == []


@pytest.mark.asyncio
async def test_expired_wave_does_not_start_fenced_memory_provider() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    memory_sink = BlockingFencedMemorySink()
    coordinator = FixedWaveCoordinator(
        runtime_state=store,
        director=MemeDirector(selected=False),
        viewer_runtime=FixedDispatchRuntime(ViewerDispatchSummary()),
        memory_extraction_sink=memory_sink,
        clock=MutableClock(1_000),
    )

    await coordinator.react(SimpleNamespace(session_id="session-1"))
    await coordinator.wait_for_background_tasks()

    assert not memory_sink.extraction_started.is_set()
    assert memory_sink.calls == []


@pytest.mark.asyncio
async def test_late_old_sequence_has_zero_side_effects_after_latest_claim() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    provider = BlockingFirstProvider()
    publisher = Recorder()
    room = Recorder()
    instance = ViewerRuntime(
        provider=provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=store,
        publisher=publisher,
        room_service=room,
        clock=FixedClock(),
        id_generator=SequenceIds(),
        max_in_flight=1,
    )
    await instance.start_session("session-1")
    pool = (await store.snapshot("session-1")).pool

    first = asyncio.create_task(
        instance.dispatch(
            wave=wave("wave-1"),
            decision=decision("wave-1"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=1)
    latest = asyncio.create_task(
        instance.dispatch(
            wave=wave("wave-2"),
            decision=decision("wave-2"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.sleep(0)
    provider.release_first.set()
    old_summary, latest_summary = await asyncio.wait_for(
        asyncio.gather(first, latest),
        timeout=1,
    )

    assert old_summary.stale == 1
    assert latest_summary.published == 1
    assert [event.text for event in publisher.events] == ["wave-2"]
    assert [event.text for event in room.events] == ["wave-2"]


@pytest.mark.asyncio
async def test_late_old_sequence_silence_is_stale_not_accepted() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    provider = BlockingFirstSilenceProvider()
    publisher = Recorder()
    room = Recorder()
    instance = ViewerRuntime(
        provider=provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=store,
        publisher=publisher,
        room_service=room,
        clock=FixedClock(),
        id_generator=SequenceIds(),
        max_in_flight=1,
    )
    await instance.start_session("session-1")
    pool = (await store.snapshot("session-1")).pool

    first = asyncio.create_task(
        instance.dispatch(
            wave=wave("wave-1"),
            decision=decision("wave-1"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(provider.first_started.wait(), timeout=1)
    latest = asyncio.create_task(
        instance.dispatch(
            wave=wave("wave-2"),
            decision=decision("wave-2"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.sleep(0)
    provider.release_first.set()
    old_summary, latest_summary = await asyncio.wait_for(
        asyncio.gather(first, latest),
        timeout=1,
    )

    assert old_summary.stale == 1
    assert old_summary.silenced == 0
    assert latest_summary.silenced == 1
    assert publisher.events == []
    assert room.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement",
    [None, state(epoch=2)],
    ids=["stop", "epoch-replace"],
)
async def test_publish_commit_finishes_before_session_transition(
    replacement: CommittedRuntime | None,
) -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    provider = BlockingFirstProvider()
    provider.release_first.set()
    publisher = BlockingPublisher()
    room = Recorder()
    instance = ViewerRuntime(
        provider=provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=store,
        publisher=publisher,
        room_service=room,
        clock=FixedClock(),
        id_generator=SequenceIds(),
        max_in_flight=1,
    )
    await instance.start_session("session-1")
    pool = (await store.snapshot("session-1")).pool
    dispatched = asyncio.create_task(
        instance.dispatch(
            wave=wave("wave-linearized"),
            decision=decision("wave-linearized"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(publisher.entered.wait(), timeout=1)

    if replacement is None:
        transitioning = asyncio.create_task(store.stop("session-1"))
    else:
        transitioning = asyncio.create_task(store.replace(replacement))
    await asyncio.sleep(0)

    assert room.events[0].text == "wave-linearized"
    assert publisher.events == []
    assert not transitioning.done()

    publisher.release.set()
    summary = await asyncio.wait_for(dispatched, timeout=1)
    await asyncio.wait_for(transitioning, timeout=1)
    published_after_transition = len(publisher.events)
    await asyncio.sleep(0)

    assert summary.published == 1
    assert [event.text for event in publisher.events] == ["wave-linearized"]
    assert len(publisher.events) == published_after_transition


@pytest.mark.asyncio
async def test_latest_sequence_claim_waits_for_in_progress_publish_commit() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    provider = BlockingFirstProvider()
    provider.release_first.set()
    publisher = BlockingPublisher()
    instance = ViewerRuntime(
        provider=provider,
        barrage_pipeline=AcceptingPipeline(),
        session_fence=store,
        publisher=publisher,
        room_service=Recorder(),
        clock=FixedClock(),
        id_generator=SequenceIds(),
        max_in_flight=1,
    )
    await instance.start_session("session-1")
    pool = (await store.snapshot("session-1")).pool
    first = asyncio.create_task(
        instance.dispatch(
            wave=wave("wave-1"),
            decision=decision("wave-1"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.wait_for(publisher.entered.wait(), timeout=1)
    latest = asyncio.create_task(
        instance.dispatch(
            wave=wave("wave-2"),
            decision=decision("wave-2"),
            pool=pool,
            runtime=SimpleNamespace(),
        )
    )
    await asyncio.sleep(0)

    assert len(provider.requests) == 1
    assert not latest.done()

    publisher.release.set()
    first_summary, latest_summary = await asyncio.wait_for(
        asyncio.gather(first, latest),
        timeout=1,
    )

    assert first_summary.published == 1
    assert latest_summary.published == 1
    assert [event.text for event in publisher.events] == ["wave-1", "wave-2"]


@pytest.mark.asyncio
async def test_retained_sequence_continues_but_reset_and_removed_claims_do_not() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    scope = {
        "room_id": "room-1",
        "session_id": "session-1",
        "viewer_instance_id": "viewer-1",
    }
    assert await store.claim_viewer_sequence(
        **scope,
        audience_epoch=1,
        viewer_sequence=1,
    )

    await store.replace(state(epoch=2))
    retained = await store.snapshot("session-1")
    assert retained.pool.viewers[0].viewer_sequence == 1
    assert await store.claim_viewer_sequence(
        **scope,
        audience_epoch=2,
        viewer_sequence=2,
    )

    await store.replace(state(epoch=3, persona_marker="reset"))
    reset = await store.snapshot("session-1")
    assert reset.pool.viewers[0].viewer_sequence == 0
    assert not await store.claim_viewer_sequence(
        **scope,
        audience_epoch=3,
        viewer_sequence=2,
    )
    assert await store.claim_viewer_sequence(
        **scope,
        audience_epoch=3,
        viewer_sequence=1,
    )

    await store.replace(state(epoch=4, persona_marker="reset", include_viewer=False))
    assert not await store.claim_viewer_sequence(
        **scope,
        audience_epoch=4,
        viewer_sequence=2,
    )


@pytest.mark.asyncio
async def test_accepts_requires_the_exact_latest_claim_and_stop_clears_it() -> None:
    store = RuntimeStateStore()
    await store.activate(state())
    scope = {
        "room_id": "room-1",
        "session_id": "session-1",
        "audience_epoch": 1,
        "viewer_instance_id": "viewer-1",
    }
    assert await store.claim_viewer_sequence(**scope, viewer_sequence=1)
    assert await store.accepts(**scope, viewer_sequence=1)
    assert await store.claim_viewer_sequence(**scope, viewer_sequence=2)
    assert not await store.accepts(**scope, viewer_sequence=1)
    assert await store.accepts(**scope, viewer_sequence=2)

    await store.stop("session-1")

    assert not await store.accepts(**scope, viewer_sequence=2)
