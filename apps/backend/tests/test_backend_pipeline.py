import asyncio
from pathlib import Path

import pytest

from advx_backend.application.ports.generation import AudienceBatch, AudienceSnapshot
from advx_backend.application.room_service import RoomSessionNotActiveError
from advx_backend.application.runtime_session_service import NoOpRuntimeCapabilityProbe
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.audience import AudienceMember
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.contracts.generation import (
    AudienceContext,
    BarrageCandidate,
    GenerationRequest,
    GenerationResult,
    Observation,
)
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
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
def runtime_spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash=f"{1:064x}",
        display_name="Persona",
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
    )
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1",
        revision=1,
        viewer_count=1,
        persona_ids=[persona.persona_id],
        persona_weights={persona.persona_id: 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=0, maximum=1),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
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
    )


def install_runtime_provider(runtime, spec: CanonicalRuntimeSpec) -> None:
    runtime.provider_controller.install_initial(
        ProviderConfigurationRequest(
            provider_profile_id=spec.provider.provider_profile_id,
            model_base_url="https://models.example/v1",
            model_name=spec.provider.viewer_model,
            director_model=spec.provider.director_model,
            viewer_model=spec.provider.viewer_model,
            memory_model=spec.provider.memory_model,
            visual_summary_model=spec.provider.visual_summary_model,
            model_api_key="test-model-key",
            asr_api_key="test-asr-key",
        )
    )


@pytest.mark.asyncio
async def test_runtime_connects_context_generation_barrage_and_room(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        local_token="test-token",
        data_directory=tmp_path,
        runtime_capability_probe=NoOpRuntimeCapabilityProbe(),
    )
    provider = RecordingModelProvider()
    barrage_subscription = await runtime.realtime_broker.subscribe_barrages()
    await runtime.startup()
    spec = runtime_spec()
    install_runtime_provider(runtime, spec)
    running = await runtime.runtime_session_service.start(
        RuntimeSessionStartRequest(
            client_request_id="pipeline-start",
            canonical_runtime_spec=spec,
            client_config_hash=spec.config_hash(),
        )
    )
    committed = await runtime.runtime_state.snapshot(running.session_id)
    assert committed.session_id == running.session_id
    assert committed.spec == spec
    assert committed.accepting_results

    try:
        await runtime.room_service.append_event(
            running.session_id,
            source_type=RoomEventSource.USER_TEXT,
            source_id="host",
            text="Did you see that?",
            payload={
                "input_id": "text-1",
                "target_persona_id": "persona-1",
            },
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
            "input_id": "text-1",
            "target_persona_id": "persona-1",
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
