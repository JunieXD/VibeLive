from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from advx_backend.application.ai_call_logging import AiCallSink
from advx_backend.application.memory_extractor import (
    OpenAICompatibleMemoryExtractor,
    RoomMemoryExtractor,
)
from advx_backend.application.ports.ingest import FrameResolver
from advx_backend.contracts.configuration import (
    ProviderConfigurationRequest,
    RuntimeModelProviderCandidate,
)
from advx_backend.contracts.viewer_runtime import (
    ProviderRuntimeSpec,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.observation_wave import FrameBundle, ObservationWave
from advx_backend.providers.model.viewer_runtime import (
    OpenAICompatibleViewerRuntimeConfig,
    OpenAICompatibleViewerRuntimeProvider,
)


class RuntimeProviderError(RuntimeError):
    pass


class RuntimeProviderUnavailableError(RuntimeProviderError):
    pass


class RuntimeProviderState(Protocol):
    async def provider_lease(
        self,
        *,
        session_id: str,
        audience_epoch: int,
    ) -> AsyncIterator[RuntimeProviderGeneration]: ...


class ViewerRuntimeProvider(Protocol):
    async def decide(self, request: object) -> CrowdDecision: ...

    async def generate(
        self,
        request: ViewerGenerationRequest,
    ) -> ViewerGenerationResponse: ...

    async def summarize(
        self,
        wave: ObservationWave,
        frame_bundle: FrameBundle,
        runtime: object,
    ) -> str: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class _GenerationLeaseState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    leases: int = 0
    retired: bool = False
    closed: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeProviderGeneration:
    provider_spec: ProviderRuntimeSpec
    configuration: ProviderConfigurationRequest = field(repr=False)
    viewer_provider: ViewerRuntimeProvider = field(repr=False)
    memory_extractor: RoomMemoryExtractor = field(repr=False)
    _state: _GenerationLeaseState = field(
        default_factory=_GenerationLeaseState,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def retired(self) -> bool:
        return self._state.retired

    @property
    def closed(self) -> bool:
        return self._state.closed

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[RuntimeProviderGeneration]:
        async with self._state.lock:
            if self._state.retired or self._state.closed:
                raise RuntimeProviderUnavailableError(
                    "runtime provider generation is retired"
                )
            self._state.leases += 1
        try:
            yield self
        finally:
            should_close = False
            async with self._state.lock:
                self._state.leases -= 1
                should_close = (
                    self._state.retired
                    and self._state.leases == 0
                    and not self._state.closed
                )
                if should_close:
                    self._state.closed = True
            if should_close:
                await self._close_components()

    async def retire(self) -> None:
        should_close = False
        async with self._state.lock:
            self._state.retired = True
            should_close = self._state.leases == 0 and not self._state.closed
            if should_close:
                self._state.closed = True
        if should_close:
            await self._close_components()

    async def _close_components(self) -> None:
        try:
            await self.memory_extractor.aclose()
        finally:
            await self.viewer_provider.aclose()


class RuntimeProviderController:
    """Build immutable model generations and keep the latest committed one."""

    def __init__(
        self,
        *,
        frame_resolver: FrameResolver,
        configuration_committer: Callable[[ProviderConfigurationRequest], None],
        ai_call_sink: AiCallSink | None = None,
    ) -> None:
        self._frame_resolver = frame_resolver
        self._configuration_committer = configuration_committer
        self._ai_call_sink = ai_call_sink
        self._active: RuntimeProviderGeneration | None = None

    def install_initial(
        self,
        request: ProviderConfigurationRequest,
        *,
        viewer_provider: ViewerRuntimeProvider | None = None,
        memory_extractor: RoomMemoryExtractor | None = None,
    ) -> RuntimeProviderGeneration:
        if self._active is not None:
            if self._active.configuration == request:
                return self._active
            raise RuntimeProviderError("a provider generation is already installed")
        generation = self.build(
            _provider_spec(request),
            request,
            viewer_provider=viewer_provider,
            memory_extractor=memory_extractor,
        )
        self._active = generation
        self._configuration_committer(request)
        return generation

    def current_for(self, spec: ProviderRuntimeSpec) -> RuntimeProviderGeneration:
        generation = self._active
        if generation is None or generation.provider_spec != spec:
            raise RuntimeProviderUnavailableError(
                "configured provider does not match the runtime provider spec"
            )
        return generation

    def build(
        self,
        spec: ProviderRuntimeSpec,
        request: ProviderConfigurationRequest | RuntimeModelProviderCandidate,
        *,
        viewer_provider: ViewerRuntimeProvider | None = None,
        memory_extractor: RoomMemoryExtractor | None = None,
    ) -> RuntimeProviderGeneration:
        _validate_candidate(spec, request)
        configuration = self._full_configuration(request)
        config = OpenAICompatibleViewerRuntimeConfig(
            base_url=configuration.model_base_url,
            provider=spec,
            api_key=configuration.model_api_key,
        )
        owned_viewer = (
            OpenAICompatibleViewerRuntimeProvider(
                config,
                frame_resolver=self._frame_resolver,
                ai_call_sink=self._ai_call_sink,
            )
            if viewer_provider is None
            else viewer_provider
        )
        owned_memory = (
            OpenAICompatibleMemoryExtractor(
                config,
                ai_call_sink=self._ai_call_sink,
            )
            if memory_extractor is None
            else memory_extractor
        )
        return RuntimeProviderGeneration(
            provider_spec=spec,
            configuration=configuration,
            viewer_provider=owned_viewer,
            memory_extractor=owned_memory,
        )

    def commit(self, generation: RuntimeProviderGeneration) -> RuntimeProviderGeneration | None:
        previous = self._active
        self._active = generation
        self._configuration_committer(generation.configuration)
        return previous

    async def aclose(self) -> None:
        active = self._active
        self._active = None
        if active is not None:
            await active.retire()

    def _full_configuration(
        self,
        request: ProviderConfigurationRequest | RuntimeModelProviderCandidate,
    ) -> ProviderConfigurationRequest:
        if isinstance(request, ProviderConfigurationRequest):
            return request
        active = self._active
        if active is None:
            raise RuntimeProviderUnavailableError(
                "an ASR configuration is required before model hot swap"
            )
        return ProviderConfigurationRequest(
            **request.model_dump(),
            asr_api_key=active.configuration.asr_api_key,
        )


class RuntimeProviderRouter:
    """Route all four model roles through the generation bound to an epoch."""

    def __init__(self, runtime_state: RuntimeProviderState) -> None:
        self._runtime_state = runtime_state

    async def decide(self, request: object) -> CrowdDecision:
        wave = getattr(request, "wave", None)
        if not isinstance(wave, ObservationWave):
            raise RuntimeProviderError("Director request is missing its runtime scope")
        async with self._runtime_state.provider_lease(
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
        ) as generation:
            return await generation.viewer_provider.decide(request)

    async def generate(
        self,
        request: ViewerGenerationRequest,
    ) -> ViewerGenerationResponse:
        async with self._runtime_state.provider_lease(
            session_id=request.session_id,
            audience_epoch=request.audience_epoch,
        ) as generation:
            return await generation.viewer_provider.generate(request)

    async def summarize(
        self,
        wave: ObservationWave,
        frame_bundle: FrameBundle,
        runtime: object,
    ) -> str:
        async with self._runtime_state.provider_lease(
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
        ) as generation:
            return await generation.viewer_provider.summarize(
                wave,
                frame_bundle,
                runtime,
            )

    async def extract(self, **kwargs: object) -> object:
        session_id = kwargs.get("session_id")
        audience_epoch = kwargs.get("audience_epoch")
        if not isinstance(session_id, str) or not isinstance(audience_epoch, int):
            raise RuntimeProviderError("Memory request is missing its runtime scope")
        async with self._runtime_state.provider_lease(
            session_id=session_id,
            audience_epoch=audience_epoch,
        ) as generation:
            return await generation.memory_extractor.extract(**kwargs)

    async def aclose(self) -> None:
        return None


def _provider_spec(
    request: ProviderConfigurationRequest | RuntimeModelProviderCandidate,
) -> ProviderRuntimeSpec:
    models = request.role_models()
    return ProviderRuntimeSpec(
        provider_profile_id=request.provider_profile_id,
        director_model=models["director"],
        viewer_model=models["viewer"],
        memory_model=models["memory"],
        visual_summary_model=models["visual_summary"],
    )


def _validate_candidate(
    spec: ProviderRuntimeSpec,
    request: ProviderConfigurationRequest | RuntimeModelProviderCandidate,
) -> None:
    if _provider_spec(request) != spec:
        raise ValueError("provider_candidate does not match canonical_runtime_spec.provider")


__all__ = [
    "RuntimeProviderController",
    "RuntimeProviderError",
    "RuntimeProviderGeneration",
    "RuntimeProviderRouter",
    "RuntimeProviderUnavailableError",
]
