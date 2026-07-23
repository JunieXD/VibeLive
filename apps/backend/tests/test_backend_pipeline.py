import asyncio
from pathlib import Path

import pytest

from advx_backend.application.ports.generation import AudienceBatch, AudienceSnapshot
from advx_backend.application.room_service import RoomSessionNotActiveError
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.audience import AudienceMember
from advx_backend.contracts.generation import (
    AudienceContext,
    BarrageCandidate,
    GenerationRequest,
    GenerationResult,
    Observation,
)
from advx_backend.domain.room import RoomEventSource


class SnapshotProvider:
    async def get_snapshot(self, *, observation: Observation) -> AudienceSnapshot:
        return AudienceSnapshot(
            session_id=observation.session_id,
            observation_id=observation.observation_id,
            audiences=(
                AudienceContext(
                    member=AudienceMember(
                        audience_id="audience-1",
                        display_name="Audience One",
                    )
                ),
            ),
        )


class AlwaysTrigger:
    async def should_generate(self, *, observation: Observation) -> bool:
        return True


class AllAudienceSelector:
    async def select_candidates(
        self,
        *,
        observation: Observation,
        snapshot: AudienceSnapshot,
    ) -> list[str]:
        return [context.member.audience_id for context in snapshot.audiences]


class SingleBatchPlanner:
    async def plan_invocations(
        self,
        *,
        observation: Observation,
        candidates: tuple[AudienceContext, ...],
    ) -> list[AudienceBatch]:
        return [AudienceBatch(tuple(context.member.audience_id for context in candidates))]


class RecordingModelProvider:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    async def health(self) -> bool:
        return True

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(
            request_id=request.request_id,
            candidates=[
                BarrageCandidate(
                    audience_id="audience-1",
                    text="  That was a clean move.  ",
                )
            ],
        )

    async def cancel(self, request_id: str) -> None:
        return None


class BlockingModelProvider(RecordingModelProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled_request_ids: list[str] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def cancel(self, request_id: str) -> None:
        self.cancelled_request_ids.append(request_id)


@pytest.mark.asyncio
async def test_runtime_connects_context_generation_barrage_and_room(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(local_token="test-token", data_directory=tmp_path)
    provider = RecordingModelProvider()
    barrage_subscription = await runtime.realtime_broker.subscribe_barrages()
    await runtime.startup()
    running = await runtime.session_service.start()
    assert running.session_id is not None

    try:
        await runtime.room_service.append_event(
            running.session_id,
            source_type=RoomEventSource.USER_TEXT,
            source_id="host",
            text="Did you see that?",
            payload={"round": 3, "tags": ["boss", "clear"]},
        )
        await runtime.context_builder.append_frame(
            running.session_id,
            mime_type="image/jpeg",
            data_ref="frame-buffer:1",
        )
        observation = await runtime.context_builder.build(
            running.session_id,
            user_context={"scene": "boss fight"},
        )
        reaction_service = runtime.build_reaction_service(
            snapshots=SnapshotProvider(),
            trigger=AlwaysTrigger(),
            selector=AllAudienceSelector(),
            invocation_planner=SingleBatchPlanner(),
            model_provider=provider,
        )

        result = await reaction_service.react(observation)
        published = await asyncio.wait_for(barrage_subscription.get(), timeout=1)
        room_events = await runtime.room_service.read_events(running.session_id)

        assert len(provider.requests) == 1
        provider_observation = provider.requests[0].observation
        assert provider_observation.session_id == observation.session_id
        assert provider_observation.frames[0].data_ref == "frame-buffer:1"
        assert provider_observation.room_events[0].payload == {
            "round": 3,
            "tags": ["boss", "clear"],
        }
        assert result.published_events == (published,)
        assert published.text == "That was a clean move."
        assert [event.source_type for event in room_events] == [
            RoomEventSource.USER_TEXT,
            RoomEventSource.AUDIENCE_BARRAGE,
        ]
        assert room_events[-1].source_id == "audience-1"
        assert room_events[-1].payload["barrage_id"] == published.barrage_id

        await runtime.session_service.stop(running.session_id)
        assert await runtime.room_service.active_session_id() is None
        with pytest.raises(RoomSessionNotActiveError):
            await runtime.context_builder.build(running.session_id)
    finally:
        current = await runtime.session_service.status()
        if current.session_id is not None:
            await runtime.session_service.stop(current.session_id)
        await runtime.realtime_broker.unsubscribe_barrages(barrage_subscription)
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_stopping_session_cancels_inflight_reaction(tmp_path: Path) -> None:
    runtime = build_runtime(local_token="test-token", data_directory=tmp_path)
    provider = BlockingModelProvider()
    barrage_subscription = await runtime.realtime_broker.subscribe_barrages()
    await runtime.startup()
    running = await runtime.session_service.start()
    assert running.session_id is not None

    try:
        observation = await runtime.context_builder.build(running.session_id)
        reaction_service = runtime.build_reaction_service(
            snapshots=SnapshotProvider(),
            trigger=AlwaysTrigger(),
            selector=AllAudienceSelector(),
            invocation_planner=SingleBatchPlanner(),
            model_provider=provider,
        )
        reaction = asyncio.create_task(reaction_service.react(observation))
        await asyncio.wait_for(provider.started.wait(), timeout=1)

        await runtime.session_service.stop(running.session_id)

        with pytest.raises(asyncio.CancelledError):
            await reaction
        assert provider.cancelled_request_ids == [provider.requests[0].request_id]
        assert barrage_subscription.empty()
        assert await runtime.room_service.active_session_id() is None
    finally:
        current = await runtime.session_service.status()
        if current.session_id is not None:
            await runtime.session_service.stop(current.session_id)
        await runtime.realtime_broker.unsubscribe_barrages(barrage_subscription)
        await runtime.shutdown()
