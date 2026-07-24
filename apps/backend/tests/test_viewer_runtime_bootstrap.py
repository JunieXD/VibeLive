import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from advx_backend import bootstrap
from advx_backend.application import runtime_provider
from advx_backend.application.reaction_service import ReactionService
from advx_backend.application.runtime_capability_probe import (
    ProductionRuntimeCapabilityProbe,
)
from advx_backend.application.runtime_state import CommittedRuntime
from advx_backend.application.shared_brain_adapters import (
    SharedBrainMemeCandidateSink,
    SharedBrainMemoryExtractionSink,
)
from advx_backend.application.viewer_pool_service import ViewerPoolSnapshot
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.application.viewer_runtime_coordinator import (
    ViewerRuntimeCoordinator,
)
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.domain.observation import Observation
from advx_backend.domain.room import RoomEvent, RoomEventSource


class FakeViewerProvider:
    def __init__(self, config, *, frame_resolver) -> None:
        self.config = config
        self.frame_resolver = frame_resolver


class FakeMemoryExtractor:
    def __init__(self, config) -> None:
        self.config = config


class FakeAsrProvider:
    def __init__(self, config) -> None:
        self.config = config


def test_provider_profile_bootstraps_viewer_runtime_pipeline_without_legacy_reaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_provider,
        "OpenAICompatibleViewerRuntimeProvider",
        FakeViewerProvider,
    )
    monkeypatch.setattr(
        runtime_provider,
        "OpenAICompatibleMemoryExtractor",
        FakeMemoryExtractor,
    )
    monkeypatch.setattr(bootstrap, "StepFunAsrProvider", FakeAsrProvider)
    runtime = bootstrap.build_runtime(
        local_token="test-local-token",
        data_directory=tmp_path,
    )

    ingest = runtime.configure_provider_profile(
        ProviderConfigurationRequest(
            provider_profile_id="profile-1",
            model_base_url="https://models.example/v1",
            model_name="shared",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
            model_api_key="model-secret",
            asr_api_key="asr-secret",
        )
    )

    assert isinstance(runtime.viewer_runtime, ViewerRuntime)
    assert isinstance(runtime.viewer_runtime_coordinator, ViewerRuntimeCoordinator)
    assert runtime.reaction_scheduler is not None
    assert runtime.reaction_scheduler._executor is runtime.viewer_runtime_coordinator
    assert not isinstance(runtime.reaction_scheduler._executor, ReactionService)
    assert ingest is runtime.ingest_service
    assert ingest._scheduler is runtime.reaction_scheduler
    assert runtime.ingest_gateway._port is ingest
    assert (
        runtime.viewer_runtime_coordinator._viewer_runtime
        is runtime.viewer_runtime
    )
    assert (
        runtime.viewer_runtime_coordinator._memory_reader
        is runtime.shared_brain_service
    )
    assert isinstance(
        runtime.viewer_runtime_coordinator._meme_sink,
        SharedBrainMemeCandidateSink,
    )
    assert (
        runtime.viewer_runtime_coordinator._meme_sink._service
        is runtime.shared_brain_service
    )
    assert isinstance(
        runtime.viewer_runtime_coordinator._memory_extraction_sink,
        SharedBrainMemoryExtractionSink,
    )
    assert (
        runtime.viewer_runtime_coordinator._memory_extraction_sink._service
        is runtime.shared_brain_service
    )
    assert runtime.viewer_runtime._provider is runtime.provider_router
    generation = runtime.provider_controller._active
    assert generation is not None
    assert isinstance(generation.viewer_provider, FakeViewerProvider)
    assert generation.viewer_provider.frame_resolver is runtime.frame_store
    assert generation.viewer_provider.config.provider.director_model == "director"
    assert generation.viewer_provider.config.provider.viewer_model == "viewer"
    capability_probe = runtime.runtime_session_service._capability_probe
    assert isinstance(capability_probe, ProductionRuntimeCapabilityProbe)
    assert capability_probe._asr_probe is not None


@pytest.mark.asyncio
async def test_bootstrap_scheduler_reads_hot_runtime_merge_window(
    tmp_path: Path,
) -> None:
    runtime = bootstrap.build_runtime(
        local_token="test-local-token",
        data_directory=tmp_path,
    )

    def committed(epoch: int, window_ms: int) -> CommittedRuntime:
        return CommittedRuntime(
            session_id="session-1",
            spec=SimpleNamespace(
                room=SimpleNamespace(room_id="room-1"),
                active_mode_id=f"mode-{epoch}",
                settings=SimpleNamespace(
                    observation_merge_window_ms=window_ms,
                ),
            ),
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

    class AcceptingSessionTasks:
        async def start_task(self, _session_id, factory, *, name=None):
            return asyncio.create_task(factory(), name=name)

        async def accepts_results(self, _session_id):
            return True

    class CapturingExecutor:
        def __init__(self) -> None:
            self.observations: list[Observation] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.result = object()

        async def react(self, observation: Observation) -> object:
            self.observations.append(observation)
            self.started.set()
            await self.release.wait()
            return self.result

    runtime.configure_ingest_pipeline(
        asr_provider=object(),
        model_provider=object(),
    )
    scheduler = runtime.reaction_scheduler
    assert scheduler is not None
    executor = CapturingExecutor()
    scheduler._executor = executor
    scheduler._session_tasks = AcceptingSessionTasks()

    await runtime.runtime_state.activate(committed(1, 20))
    now_ms = runtime.clock.now_ms()
    first = await scheduler.submit(
        Observation(
            session_id="session-1",
            observation_id="first",
            created_at_ms=now_ms,
            room_events=(
                RoomEvent(
                    event_id="event-1",
                    session_id="session-1",
                    sequence=1,
                    source_type=RoomEventSource.USER_TEXT,
                    created_at_ms=now_ms,
                    text="first",
                ),
            ),
            trigger_event_ids=("event-1",),
        )
    )
    latest = await scheduler.submit(
        Observation(
            session_id="session-1",
            observation_id="latest",
            created_at_ms=now_ms,
            room_events=(
                RoomEvent(
                    event_id="event-2",
                    session_id="session-1",
                    sequence=2,
                    source_type=RoomEventSource.USER_TEXT,
                    created_at_ms=now_ms,
                    text="latest",
                ),
            ),
            trigger_event_ids=("event-2",),
        )
    )
    await asyncio.wait_for(executor.started.wait(), timeout=1)
    executor.release.set()

    assert await first is executor.result
    assert await latest is executor.result
    assert executor.observations[0].trigger_event_ids == ("event-1", "event-2")

    await runtime.runtime_state.replace(committed(2, 0))
    assert await runtime.observation_merge_window_ms("session-1") == 0
