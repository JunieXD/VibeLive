import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from advx_backend.application.builtin_audiences import BUILTIN_AUDIENCES
from advx_backend.application.frame_store import FrameStoreSessionNotActiveError
from advx_backend.application.ports.asr import AudioChunk, TranscriptSegment
from advx_backend.application.ports.ingest import FrameInput, TextInput
from advx_backend.application.runtime_session_service import NoOpRuntimeCapabilityProbe
from advx_backend.bootstrap import (
    DATA_DIRECTORY_ENV,
    LOCAL_TOKEN_ENV,
    PipelineConfig,
    build_runtime,
    build_runtime_from_environment,
)
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.contracts.generation import GenerationRequest, GenerationResult
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.room import RoomEventSource


class RecordingAsrProvider:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.results_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()

    async def start(self) -> None:
        self.started += 1

    async def push_audio(self, chunk: AudioChunk) -> None:
        del chunk

    async def commit(self) -> None:
        pass

    async def results(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            yield await self.results_queue.get()

    async def stop(self) -> None:
        self.stopped += 1


class RecordingModelProvider:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    async def health(self) -> bool:
        return True

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(request_id=request.request_id, candidates=[])

    async def cancel(self, request_id: str) -> None:
        del request_id


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


def test_runtime_reads_ephemeral_local_token_without_revealing_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    token = "injected-local-token"
    monkeypatch.setenv(LOCAL_TOKEN_ENV, token)
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))

    runtime = build_runtime_from_environment()

    assert runtime.local_token == token
    assert runtime.database.path == tmp_path / "advx.sqlite3"
    assert token not in repr(runtime)


@pytest.mark.asyncio
async def test_runtime_initializes_audiences_and_uses_default_generation_policies(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        local_token="local-token",
        data_directory=tmp_path,
        runtime_capability_probe=NoOpRuntimeCapabilityProbe(),
    )
    model = RecordingModelProvider()
    await runtime.startup()
    try:
        spec = runtime_spec()
        install_runtime_provider(runtime, spec)
        running = await runtime.runtime_session_service.start(
            RuntimeSessionStartRequest(
                client_request_id="bootstrap-generation-start",
                canonical_runtime_spec=spec,
                client_config_hash=spec.config_hash(),
            )
        )
        committed = await runtime.runtime_state.snapshot(running.session_id)
        assert committed.session_id == running.session_id
        assert committed.spec == spec
        assert committed.accepting_results
        await runtime.room_service.append_event(
            running.session_id,
            source_type=RoomEventSource.USER_TEXT,
            source_id="host",
            text="hello",
        )
        observation = await runtime.context_builder.build(running.session_id)
        snapshot = await runtime.audience_service.get_snapshot(observation=observation)
        generation = runtime.build_generation_service(model_provider=model)

        outputs = await generation.generate(observation)

        assert {context.member.audience_id for context in snapshot.audiences} == {
            template.audience_id for template in BUILTIN_AUDIENCES
        }
        assert len(outputs) == 2
        assert [len(request.audiences) for request in model.requests] == [2, 1]
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_configured_ingest_pipeline_follows_session_lifecycle_and_config(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(
        local_token="local-token",
        data_directory=tmp_path,
        pipeline_config=PipelineConfig(ingest_max_tracked_input_ids=1),
        runtime_capability_probe=NoOpRuntimeCapabilityProbe(),
    )
    asr = RecordingAsrProvider()
    model = RecordingModelProvider()
    ingest = runtime.configure_ingest_pipeline(
        asr_provider=asr,
        model_provider=model,
    )
    await runtime.startup()
    try:
        spec = runtime_spec()
        install_runtime_provider(runtime, spec)
        running = await runtime.runtime_session_service.start(
            RuntimeSessionStartRequest(
                client_request_id="bootstrap-ingest-start",
                canonical_runtime_spec=spec,
                client_config_hash=spec.config_hash(),
            )
        )
        committed = await runtime.runtime_state.snapshot(running.session_id)
        assert committed.session_id == running.session_id
        assert committed.spec == spec
        assert committed.accepting_results
        receipt = await ingest.submit_frame(
            FrameInput(
                session_id=running.session_id,
                input_id="frame-1",
                captured_at_ms=runtime.clock.now_ms(),
                mime_type="image/jpeg",
                body=b"pixels",
            )
        )
        observation = await runtime.context_builder.build(running.session_id)
        frame = observation.frames[0]
        for input_id in ("text-1", "text-2", "text-1"):
            await ingest.submit_text(
                TextInput(
                    session_id=running.session_id,
                    input_id=input_id,
                    created_at_ms=runtime.clock.now_ms(),
                    text=input_id,
                )
            )

        assert asr.started == 1
        assert receipt.input_id == "frame-1"

        await runtime.session_service.stop(running.session_id)

        assert asr.stopped == 1
        with pytest.raises(FrameStoreSessionNotActiveError):
            await runtime.frame_store.resolve(
                session_id=running.session_id,
                frame=frame,
            )
    finally:
        await runtime.shutdown()
