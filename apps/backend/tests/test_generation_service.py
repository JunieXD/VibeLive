import asyncio
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, TypeVar

import pytest

from advx_backend.application.generation_service import GenerationService
from advx_backend.application.ports.generation import (
    AudienceBatch,
    AudienceSnapshot,
)
from advx_backend.contracts.audience import AudienceMember, AudienceMemory
from advx_backend.contracts.generation import (
    AudienceContext,
    BarrageCandidate,
    GenerationRequest,
    GenerationResult,
    Observation,
)

T = TypeVar("T")


class SequenceIdGenerator:
    def __init__(self) -> None:
        self._value = 0

    def new_id(self) -> str:
        self._value += 1
        return f"request-{self._value}"


class MutableSessionTasks:
    def __init__(self, session_id: str = "session-1") -> None:
        self.session_id = session_id
        self.accepting = True
        self.tasks: set[asyncio.Task[Any]] = set()

    async def start_task(
        self,
        session_id: str,
        factory: Callable[[], Coroutine[Any, Any, T]],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        if not self.accepting or session_id != self.session_id:
            raise RuntimeError("session is not accepting work")
        task = asyncio.create_task(factory(), name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def accepts_results(self, session_id: str) -> bool:
        return self.accepting and session_id == self.session_id


class StaticSnapshotProvider:
    def __init__(self, audiences: Sequence[AudienceContext]) -> None:
        self.audiences = tuple(audiences)
        self.observations: list[Observation] = []

    async def get_snapshot(self, *, observation: Observation) -> AudienceSnapshot:
        self.observations.append(observation)
        return AudienceSnapshot(
            session_id=observation.session_id,
            observation_id=observation.observation_id,
            audiences=self.audiences,
        )


class AlwaysTrigger:
    def __init__(self) -> None:
        self.observations: list[Observation] = []

    async def should_generate(self, *, observation: Observation) -> bool:
        self.observations.append(observation)
        return True


class StaticSelector:
    def __init__(self, audience_ids: Sequence[str]) -> None:
        self.audience_ids = tuple(audience_ids)
        self.calls = 0

    async def select_candidates(
        self,
        *,
        observation: Observation,
        snapshot: AudienceSnapshot,
    ) -> Sequence[str]:
        del observation, snapshot
        self.calls += 1
        return self.audience_ids


class StaticInvocationPlanner:
    def __init__(self, batches: Sequence[AudienceBatch]) -> None:
        self.batches = tuple(batches)
        self.calls = 0
        self.candidates: tuple[AudienceContext, ...] = ()

    async def plan_invocations(
        self,
        *,
        observation: Observation,
        candidates: Sequence[AudienceContext],
    ) -> Sequence[AudienceBatch]:
        del observation
        self.calls += 1
        self.candidates = tuple(candidates)
        return self.batches


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []
        self.cancelled_request_ids: list[str] = []

    async def health(self) -> bool:
        return True

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        audience_id = request.audiences[0].member.audience_id
        return GenerationResult(
            request_id=request.request_id,
            candidates=[BarrageCandidate(audience_id=audience_id, text="hello")],
        )

    async def cancel(self, request_id: str) -> None:
        self.cancelled_request_ids.append(request_id)


class Harness:
    def __init__(
        self,
        *,
        provider: RecordingProvider,
        audiences: Sequence[AudienceContext],
        selected_ids: Sequence[str],
        batches: Sequence[AudienceBatch],
        max_concurrency: int = 4,
    ) -> None:
        self.sessions = MutableSessionTasks()
        self.snapshots = StaticSnapshotProvider(audiences)
        self.trigger = AlwaysTrigger()
        self.selector = StaticSelector(selected_ids)
        self.planner = StaticInvocationPlanner(batches)
        self.service = GenerationService(
            snapshots=self.snapshots,
            trigger=self.trigger,
            selector=self.selector,
            invocation_planner=self.planner,
            model_provider=provider,
            session_tasks=self.sessions,
            id_generator=SequenceIdGenerator(),
            max_concurrency=max_concurrency,
        )


def audience_context(audience_id: str) -> AudienceContext:
    return AudienceContext(
        member=AudienceMember(
            audience_id=audience_id,
            display_name=f"Audience {audience_id}",
            personality={"temperament": "curious"},
            preferences={"topics": ["games"]},
            speaking_style={"length": "short"},
            relationships={"host": "friendly"},
        ),
        memories=[
            AudienceMemory(
                memory_id=f"memory-{audience_id}",
                audience_id=audience_id,
                content="A prior public interaction",
                source_event_ids=["event-previous"],
                created_at_ms=10,
                updated_at_ms=20,
            )
        ],
        session_state={"mood": "engaged"},
    )


def observation(observation_id: str = "observation-1") -> Observation:
    return Observation(
        session_id="session-1",
        observation_id=observation_id,
        created_at_ms=100,
        user_context={"scene": "boss fight"},
    )


@pytest.mark.asyncio
async def test_zero_candidates_do_not_call_model() -> None:
    provider = RecordingProvider()
    context = audience_context("audience-1")
    harness = Harness(
        provider=provider,
        audiences=[context],
        selected_ids=[],
        batches=[AudienceBatch(("audience-1",))],
    )

    results = await harness.service.generate(observation())

    assert results == ()
    assert provider.requests == []
    assert harness.planner.calls == 0


@pytest.mark.asyncio
async def test_generation_request_contains_complete_work_item_fields() -> None:
    provider = RecordingProvider()
    first = audience_context("audience-1")
    second = audience_context("audience-2")
    harness = Harness(
        provider=provider,
        audiences=[first, second],
        selected_ids=["audience-1", "audience-2"],
        batches=[AudienceBatch(("audience-1", "audience-2"))],
    )
    current_observation = observation()

    results = await harness.service.generate(current_observation)

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.request_id == "request-1"
    assert request.observation == current_observation
    assert request.observation.session_id == "session-1"
    assert request.observation.observation_id == "observation-1"
    assert request.audiences == [first, second]
    assert [result.request_id for result in results] == ["request-1"]


class UnknownAudienceProvider(RecordingProvider):
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(
            request_id=request.request_id,
            candidates=[
                BarrageCandidate(audience_id="audience-1", text="known"),
                BarrageCandidate(audience_id="unknown", text="spoofed"),
            ],
        )


@pytest.mark.asyncio
async def test_unknown_provider_audience_is_dropped() -> None:
    provider = UnknownAudienceProvider()
    harness = Harness(
        provider=provider,
        audiences=[audience_context("audience-1")],
        selected_ids=["audience-1"],
        batches=[AudienceBatch(("audience-1",))],
    )

    results = await harness.service.generate(observation())

    assert len(results) == 1
    assert [candidate.audience_id for candidate in results[0].candidates] == ["audience-1"]


class BlockingProvider(RecordingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return GenerationResult(
            request_id=request.request_id,
            candidates=[BarrageCandidate(audience_id="audience-1", text="late")],
        )


@pytest.mark.asyncio
async def test_late_result_is_dropped_after_session_replacement() -> None:
    provider = BlockingProvider()
    harness = Harness(
        provider=provider,
        audiences=[audience_context("audience-1")],
        selected_ids=["audience-1"],
        batches=[AudienceBatch(("audience-1",))],
    )
    generation = asyncio.create_task(harness.service.generate(observation()))
    await provider.started.wait()

    harness.sessions.session_id = "session-2"
    provider.release.set()

    assert await generation == ()


class SelectiveFailureProvider(RecordingProvider):
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        audience_id = request.audiences[0].member.audience_id
        if audience_id == "audience-1":
            raise RuntimeError("provider failed for one request")
        return GenerationResult(
            request_id=request.request_id,
            candidates=[BarrageCandidate(audience_id=audience_id, text="success")],
        )


@pytest.mark.asyncio
async def test_provider_exception_is_isolated_from_other_requests() -> None:
    provider = SelectiveFailureProvider()
    harness = Harness(
        provider=provider,
        audiences=[audience_context("audience-1"), audience_context("audience-2")],
        selected_ids=["audience-1", "audience-2"],
        batches=[
            AudienceBatch(("audience-1",)),
            AudienceBatch(("audience-2",)),
        ],
    )

    results = await harness.service.generate(observation())

    assert [request.request_id for request in provider.requests] == ["request-1", "request-2"]
    assert [result.request_id for result in results] == ["request-2"]
    assert results[0].candidates[0].audience_id == "audience-2"


class ConcurrencyTrackingProvider(RecordingProvider):
    def __init__(self, expected_concurrency: int) -> None:
        super().__init__()
        self.expected_concurrency = expected_concurrency
        self.active = 0
        self.max_active = 0
        self.limit_reached = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active == self.expected_concurrency:
            self.limit_reached.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1
        audience_id = request.audiences[0].member.audience_id
        return GenerationResult(
            request_id=request.request_id,
            candidates=[BarrageCandidate(audience_id=audience_id, text="done")],
        )


@pytest.mark.asyncio
async def test_model_task_creation_and_calls_respect_concurrency_limit() -> None:
    audience_ids = [f"audience-{index}" for index in range(6)]
    provider = ConcurrencyTrackingProvider(expected_concurrency=2)
    harness = Harness(
        provider=provider,
        audiences=[audience_context(audience_id) for audience_id in audience_ids],
        selected_ids=audience_ids,
        batches=[AudienceBatch((audience_id,)) for audience_id in audience_ids],
        max_concurrency=2,
    )
    generation = asyncio.create_task(harness.service.generate(observation()))

    await asyncio.wait_for(provider.limit_reached.wait(), timeout=1)
    await asyncio.sleep(0)
    model_tasks = [
        task for task in asyncio.all_tasks() if task.get_name().startswith("generation-model:")
    ]

    assert len(provider.requests) == 2
    assert len(model_tasks) == 2
    assert provider.max_active == 2

    provider.release.set()
    results = await asyncio.wait_for(generation, timeout=1)

    assert len(provider.requests) == 6
    assert len(results) == 6
    assert provider.max_active == 2


class CancellableProvider(RecordingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancel_called = asyncio.Event()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def cancel(self, request_id: str) -> None:
        await super().cancel(request_id)
        self.cancel_called.set()


@pytest.mark.asyncio
async def test_task_cancellation_cancels_provider_request() -> None:
    provider = CancellableProvider()
    harness = Harness(
        provider=provider,
        audiences=[audience_context("audience-1")],
        selected_ids=["audience-1"],
        batches=[AudienceBatch(("audience-1",))],
    )
    generation = asyncio.create_task(harness.service.generate(observation()))
    await provider.started.wait()

    generation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await generation
    await asyncio.wait_for(provider.cancel_called.wait(), timeout=1)
    assert provider.cancelled_request_ids == ["request-1"]


class CrossedRequestProvider(RecordingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.both_started = asyncio.Event()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if len(self.requests) == 2:
            self.both_started.set()
        await self.both_started.wait()
        crossed_id = "request-2" if request.request_id == "request-1" else "request-1"
        audience_id = request.audiences[0].member.audience_id
        return GenerationResult(
            request_id=crossed_id,
            candidates=[BarrageCandidate(audience_id=audience_id, text="crossed")],
        )


@pytest.mark.asyncio
async def test_request_ids_cannot_cross_between_concurrent_calls() -> None:
    provider = CrossedRequestProvider()
    harness = Harness(
        provider=provider,
        audiences=[audience_context("audience-1"), audience_context("audience-2")],
        selected_ids=["audience-1", "audience-2"],
        batches=[
            AudienceBatch(("audience-1",)),
            AudienceBatch(("audience-2",)),
        ],
        max_concurrency=2,
    )

    results = await harness.service.generate(observation())

    assert [request.request_id for request in provider.requests] == ["request-1", "request-2"]
    assert results == ()
