import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from advx_backend.application.runtime_provider import (
    RuntimeProviderController,
    RuntimeProviderGeneration,
    RuntimeProviderRouter,
)
from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.viewer_pool_service import ViewerPoolSnapshot
from advx_backend.contracts.configuration import (
    ProviderConfigurationRequest,
    RuntimeModelProviderCandidate,
)
from advx_backend.contracts.viewer_runtime import ProviderRuntimeSpec


class FakeViewerProvider:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeMemoryExtractor:
    def __init__(self, value: str, *, blocked: bool = False) -> None:
        self.value = value
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        if not blocked:
            self.release.set()

    async def extract(self, **_: object) -> str:
        self.entered.set()
        await self.release.wait()
        return self.value

    async def aclose(self) -> None:
        self.closed = True


def provider_spec(name: str) -> ProviderRuntimeSpec:
    return ProviderRuntimeSpec(
        provider_profile_id=name,
        director_model=f"{name}-director",
        viewer_model=f"{name}-viewer",
        memory_model=f"{name}-memory",
        visual_summary_model=f"{name}-visual",
    )


def configuration(name: str) -> ProviderConfigurationRequest:
    return ProviderConfigurationRequest(
        provider_profile_id=name,
        model_base_url=f"https://{name}.example/v1",
        model_name=f"{name}-default",
        director_model=f"{name}-director",
        viewer_model=f"{name}-viewer",
        memory_model=f"{name}-memory",
        visual_summary_model=f"{name}-visual",
        model_api_key=f"{name}-model-secret",
        asr_api_key="stable-asr-secret",
    )


def generation(
    name: str,
    *,
    blocked: bool = False,
) -> tuple[RuntimeProviderGeneration, FakeViewerProvider, FakeMemoryExtractor]:
    viewer = FakeViewerProvider()
    memory = FakeMemoryExtractor(name, blocked=blocked)
    return (
        RuntimeProviderGeneration(
            provider_spec=provider_spec(name),
            configuration=configuration(name),
            viewer_provider=cast(Any, viewer),
            memory_extractor=cast(Any, memory),
        ),
        viewer,
        memory,
    )


def committed(
    epoch: int,
    active_generation: RuntimeProviderGeneration,
) -> CommittedRuntime:
    mode = SimpleNamespace(mode_id="mode-1", persona_overrides={})
    return CommittedRuntime(
        session_id="session-1",
        spec=cast(
            Any,
            SimpleNamespace(
                room=SimpleNamespace(room_id="room-1"),
                active_mode_id="mode-1",
                modes=[mode],
                personas=[],
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
        provider_generation=active_generation,
    )


@pytest.mark.asyncio
async def test_router_keeps_retired_generation_alive_until_its_lease_finishes() -> None:
    store = RuntimeStateStore()
    old, old_viewer, old_memory = generation("old", blocked=True)
    new, _, _ = generation("new")
    await store.activate(committed(1, old))
    router = RuntimeProviderRouter(store)

    old_call = asyncio.create_task(
        router.extract(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            events=(),
            current_revision=0,
        )
    )
    await old_memory.entered.wait()

    async def persist() -> None:
        return None

    await store.replace_after(committed(2, new), persist)
    await old.retire()

    assert not old_memory.closed
    assert not old_viewer.closed
    assert (
        await router.extract(
            room_id="room-1",
            session_id="session-1",
            audience_epoch=2,
            events=(),
            current_revision=0,
        )
        == "new"
    )

    old_memory.release.set()
    assert await old_call == "old"
    assert old_memory.closed
    assert old_viewer.closed


def test_model_candidate_omits_asr_and_keeps_credentials_out_of_repr() -> None:
    candidate = RuntimeModelProviderCandidate(
        provider_profile_id="next",
        model_base_url="https://next.example/v1",
        model_name="next-default",
        director_model="next-director",
        viewer_model="next-viewer",
        memory_model="next-memory",
        visual_summary_model="next-visual",
        model_api_key="next-model-secret",
    )
    controller = RuntimeProviderController(
        frame_resolver=cast(Any, object()),
        configuration_committer=lambda _: None,
    )
    initial, viewer, memory = generation("old")
    controller._active = initial

    built = controller.build(
        provider_spec("next"),
        candidate,
        viewer_provider=cast(Any, viewer),
        memory_extractor=cast(Any, memory),
    )

    assert built.configuration.asr_api_key == "stable-asr-secret"
    serialized = repr(candidate) + repr(built)
    assert "next-model-secret" not in serialized
    assert "stable-asr-secret" not in serialized
    assert "asr_api_key" not in candidate.model_dump()
