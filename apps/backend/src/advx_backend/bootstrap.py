import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from advx_backend.application.audience_service import AudienceService
from advx_backend.application.barrage_pipeline import BarragePipeline
from advx_backend.application.context_builder import ContextBuilder
from advx_backend.application.debug_service import DebugService
from advx_backend.application.frame_metadata import StoredFrameMetadataResolver
from advx_backend.application.frame_store import InMemoryFrameStore
from advx_backend.application.generation_policies import (
    DefaultAudienceSelector,
    DefaultGenerationInvocationPlanner,
    DefaultGenerationTrigger,
)
from advx_backend.application.generation_service import GenerationService
from advx_backend.application.ingest_gateway import IngestGateway
from advx_backend.application.ingest_service import IngestService
from advx_backend.application.memory_extractor import RoomMemoryExtractor
from advx_backend.application.ports.asr import AsrProvider
from advx_backend.application.ports.generation import (
    AudienceSelector,
    AudienceSnapshotProvider,
    GenerationInvocationPlanner,
    GenerationTrigger,
)
from advx_backend.application.ports.ingest import FrameStoreLimits
from advx_backend.application.ports.model import ModelProvider
from advx_backend.application.ports.persistence import UnitOfWorkFactory
from advx_backend.application.reaction_scheduler import LatestWinsReactionScheduler
from advx_backend.application.reaction_service import ReactionService
from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.replay_service import ReplayService
from advx_backend.application.room_event_persistence import (
    PersistentRuntimeRoomEventStore,
)
from advx_backend.application.room_service import RoomService
from advx_backend.application.runtime_capability_probe import (
    ProductionRuntimeCapabilityProbe,
    create_stepfun_final_audio_probe,
)
from advx_backend.application.runtime_config_service import RuntimeCapabilityProbe
from advx_backend.application.runtime_provider import (
    RuntimeProviderController,
    RuntimeProviderRouter,
)
from advx_backend.application.runtime_session_service import RuntimeSessionService
from advx_backend.application.runtime_state import RuntimeStateStore
from advx_backend.application.session_resources import SessionResources
from advx_backend.application.session_service import SessionService
from advx_backend.application.shared_brain_adapters import (
    SharedBrainMemeCandidateSink,
    SharedBrainMemoryExtractionSink,
)
from advx_backend.application.shared_brain_service import SharedBrainService
from advx_backend.application.transcript_target_resolver import (
    RuntimeTranscriptTargetResolver,
)
from advx_backend.application.viewer_audience_service import ViewerAudienceService
from advx_backend.application.viewer_barrage_pipeline import ViewerBarragePipeline
from advx_backend.application.viewer_behavior_service import ViewerBehaviorService
from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.application.viewer_runtime import ViewerRuntime
from advx_backend.application.viewer_runtime_adapters import (
    PersistentViewerRoomWriter,
    RealtimeViewerBarragePublisher,
)
from advx_backend.application.viewer_runtime_coordinator import (
    ViewerRuntimeCoordinator,
)
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.domain.barrage import BarragePolicy
from advx_backend.infrastructure.logging import AiCallStore, TraceStore, configure_logging
from advx_backend.infrastructure.persistence.sqlite import (
    DatabaseConfig,
    SQLiteDatabase,
    SQLiteSessionRecordStore,
    SQLiteUnitOfWorkFactory,
)
from advx_backend.infrastructure.security.local_token import create_local_token
from advx_backend.infrastructure.system import SystemClock, UuidIdGenerator
from advx_backend.providers.asr import StepFunAsrConfig, StepFunAsrProvider
from advx_backend.providers.model import OpenAICompatibleProvider

BACKEND_VERSION = "0.1.0"
logger = logging.getLogger(__name__)
LOCAL_TOKEN_ENV = "ADVX_LOCAL_TOKEN"
DATA_DIRECTORY_ENV = "ADVX_DATA_DIR"
MODEL_BASE_URL_ENV = "ADVX_MODEL_BASE_URL"
MODEL_NAME_ENV = "ADVX_MODEL_NAME"
MODEL_API_KEY_ENV = "ADVX_MODEL_API_KEY"
ASR_API_KEY_ENV = "ADVX_ASR_API_KEY"
DEFAULT_DATA_DIRECTORY = Path.cwd() / ".advx-data"


@dataclass(frozen=True)
class PipelineConfig:
    room_event_capacity: int | None = None
    room_event_ttl_ms: int | None = None
    frame_capacity: int = 120
    frame_ttl_ms: int = 120_000
    max_frames_per_observation: int = 120
    max_events_per_observation: int | None = None
    frame_max_bytes: int = 4_194_304
    frame_total_bytes: int = 536_870_912
    audience_max_memories: int = 12
    ingest_max_tracked_input_ids: int = 1_024
    barrage_max_text_length: int = 200
    barrage_ttl_ms: int = 15_000
    barrage_blocked_words: frozenset[str] = frozenset()
    barrage_duplicate_window_ms: int = 30_000
    barrage_max_duplicate_entries: int = 256
    barrage_density_window_ms: int = 10_000
    barrage_max_outputs_per_window: int = 6
    barrage_max_tracked_sessions: int = 2


@dataclass(frozen=True)
class ExternalProviderConfig:
    model_base_url: str
    model_name: str
    model_api_key: str = field(repr=False)
    asr_api_key: str = field(repr=False)
    asr_base_url: str = "https://api.stepfun.com/step_plan/v1"
    asr_model: str = "stepaudio-2.5-asr"

    def __post_init__(self) -> None:
        for field_name in (
            "model_base_url",
            "model_name",
            "model_api_key",
            "asr_api_key",
            "asr_base_url",
            "asr_model",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value.strip())


class ProviderPipelineAlreadyConfiguredError(RuntimeError):
    pass


@dataclass
class ProviderConfigurationStore:
    request: ProviderConfigurationRequest | None = field(
        default=None,
        repr=False,
    )

    def current(self) -> ProviderConfigurationRequest | None:
        return self.request

    def set(self, request: ProviderConfigurationRequest) -> None:
        self.request = request

    def clear(self) -> None:
        self.request = None


@dataclass
class BackendRuntime:
    session_service: SessionService
    realtime_broker: RealtimeBroker
    database: SQLiteDatabase
    unit_of_work_factory: UnitOfWorkFactory
    session_record_store: SQLiteSessionRecordStore
    clock: SystemClock
    id_generator: UuidIdGenerator
    room_service: RoomService
    context_builder: ContextBuilder
    audience_service: AudienceService
    frame_store: InMemoryFrameStore
    generation_trigger: DefaultGenerationTrigger
    audience_selector: DefaultAudienceSelector
    invocation_planner: DefaultGenerationInvocationPlanner
    barrage_pipeline: BarragePipeline
    session_resources: SessionResources
    ingest_gateway: IngestGateway
    debug_service: DebugService
    replay_service: ReplayService
    shared_brain_service: SharedBrainService
    room_event_store: PersistentRuntimeRoomEventStore
    runtime_session_service: RuntimeSessionService
    runtime_state: RuntimeStateStore
    viewer_audience_service: ViewerAudienceService
    provider_configuration_store: ProviderConfigurationStore
    provider_controller: RuntimeProviderController
    provider_router: RuntimeProviderRouter
    pipeline_config: PipelineConfig
    local_token: str = field(repr=False)
    ingest_service: IngestService | None = field(default=None, init=False)
    reaction_scheduler: LatestWinsReactionScheduler | None = field(default=None, init=False)
    viewer_runtime: ViewerRuntime | None = field(default=None, init=False)
    viewer_runtime_coordinator: ViewerRuntimeCoordinator | None = field(
        default=None,
        init=False,
    )
    external_provider_config: ExternalProviderConfig | None = field(default=None, init=False)
    _owned_model_provider: OpenAICompatibleProvider | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _owned_asr_provider: StepFunAsrProvider | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _started: bool = field(default=False, init=False, repr=False)

    async def startup(self) -> None:
        if self._started:
            return
        try:
            await self.database.start()
            await self.session_record_store.recover_interrupted(ended_at_ms=self.clock.now_ms())
        except Exception as error:
            # Keep the control plane alive for machine-readable health and recovery.
            logger.exception("SQLite startup failed; runtime is persistence-degraded")
            if self.database.started:
                await self.database.mark_startup_failed(
                    code="sqlite_recovery_failed",
                    error=error,
                )
            return
        await self.audience_service.initialize_builtin_audiences()
        self._started = True

    async def shutdown(self) -> None:
        try:
            await self.session_service.shutdown()
        finally:
            try:
                if self.viewer_runtime_coordinator is not None:
                    await self.viewer_runtime_coordinator.wait_for_background_tasks()
            finally:
                try:
                    await self.provider_controller.aclose()
                finally:
                    try:
                        if self._owned_model_provider is not None:
                            await self._owned_model_provider.aclose()
                    finally:
                        self._owned_model_provider = None
                        self._owned_asr_provider = None
                        self.external_provider_config = None
                        self.provider_configuration_store.clear()
                        self.ingest_service = None
                        self.reaction_scheduler = None
                        self.viewer_runtime = None
                        self.viewer_runtime_coordinator = None
                        self.ingest_gateway.clear()
                        await self.database.close()
                        self._started = False

    def build_generation_service(
        self,
        *,
        model_provider: ModelProvider,
        snapshots: AudienceSnapshotProvider | None = None,
        trigger: GenerationTrigger | None = None,
        selector: AudienceSelector | None = None,
        invocation_planner: GenerationInvocationPlanner | None = None,
        max_concurrency: int = 4,
    ) -> GenerationService:
        return GenerationService(
            snapshots=self.audience_service if snapshots is None else snapshots,
            trigger=self.generation_trigger if trigger is None else trigger,
            selector=self.audience_selector if selector is None else selector,
            invocation_planner=(
                self.invocation_planner if invocation_planner is None else invocation_planner
            ),
            model_provider=model_provider,
            session_tasks=self.session_service,
            id_generator=self.id_generator,
            max_concurrency=max_concurrency,
        )

    def build_reaction_service(
        self,
        *,
        model_provider: ModelProvider,
        snapshots: AudienceSnapshotProvider | None = None,
        trigger: GenerationTrigger | None = None,
        selector: AudienceSelector | None = None,
        invocation_planner: GenerationInvocationPlanner | None = None,
        max_concurrency: int = 4,
    ) -> ReactionService:
        generation_service = self.build_generation_service(
            snapshots=snapshots,
            trigger=trigger,
            selector=selector,
            invocation_planner=invocation_planner,
            model_provider=model_provider,
            max_concurrency=max_concurrency,
        )
        return ReactionService(
            generation_service=generation_service,
            barrage_pipeline=self.barrage_pipeline,
            room_service=self.room_service,
            session_tasks=self.session_service,
            publisher=self.realtime_broker,
        )

    async def observation_merge_window_ms(self, session_id: str) -> int:
        try:
            committed = await self.runtime_state.snapshot(session_id)
        except KeyError:
            return 0
        return committed.spec.settings.observation_merge_window_ms

    async def ambient_enabled(self, session_id: str) -> bool:
        try:
            committed = await self.runtime_state.snapshot(session_id)
        except KeyError:
            return False
        mode = next(
            (
                item
                for item in committed.spec.modes
                if item.mode_id == committed.spec.active_mode_id
            ),
            None,
        )
        return mode is not None and mode.ambience.value == "continuous"

    def configure_ingest_pipeline(
        self,
        *,
        asr_provider: AsrProvider,
        model_provider: ModelProvider,
        max_concurrency: int = 4,
    ) -> IngestService:
        if self.ingest_service is not None:
            raise RuntimeError("the ingest pipeline is already configured")
        reaction_service = self.build_reaction_service(
            model_provider=model_provider,
            max_concurrency=max_concurrency,
        )
        scheduler = LatestWinsReactionScheduler(
            executor=reaction_service,
            session_tasks=self.session_service,
            clock=self.clock,
            merge_window_provider=self.observation_merge_window_ms,
        )
        ingest_service = IngestService(
            room_service=self.room_service,
            context_builder=self.context_builder,
            frame_store=self.frame_store,
            asr_provider=asr_provider,
            scheduler=scheduler,
            session_tasks=self.session_service,
            clock=self.clock,
            max_tracked_input_ids=self.pipeline_config.ingest_max_tracked_input_ids,
            voice_target_resolver=RuntimeTranscriptTargetResolver(self.runtime_state),
            ambient_enabled=self.ambient_enabled,
        )
        self.session_resources.add_resource(ingest_service)
        self.ingest_gateway.configure(ingest_service)
        self.reaction_scheduler = scheduler
        self.ingest_service = ingest_service
        return ingest_service

    def configure_external_provider_pipeline(
        self,
        config: ExternalProviderConfig,
    ) -> IngestService:
        request = ProviderConfigurationRequest(
            provider_profile_id="default",
            model_base_url=config.model_base_url,
            model_name=config.model_name,
            model_api_key=config.model_api_key,
            asr_api_key=config.asr_api_key,
        )
        return self._configure_viewer_runtime_pipeline(
            request=request,
            external_config=config,
        )

    def configure_provider_profile(
        self,
        request: ProviderConfigurationRequest,
    ) -> IngestService:
        return self._configure_viewer_runtime_pipeline(
            request=request,
            external_config=ExternalProviderConfig(
                model_base_url=request.model_base_url,
                model_name=request.model_name,
                model_api_key=request.model_api_key,
                asr_api_key=request.asr_api_key,
            ),
        )

    def _configure_viewer_runtime_pipeline(
        self,
        *,
        request: ProviderConfigurationRequest,
        external_config: ExternalProviderConfig,
        viewer_provider_override: object | None = None,
        memory_extractor_override: RoomMemoryExtractor | None = None,
        asr_provider_override: AsrProvider | None = None,
    ) -> IngestService:
        configured = self.provider_configuration_store.current()
        if configured is not None:
            if configured == request:
                assert self.ingest_service is not None
                return self.ingest_service
            raise ProviderPipelineAlreadyConfiguredError(
                "a different external provider pipeline is already configured"
            )
        if self.ingest_service is not None:
            raise ProviderPipelineAlreadyConfiguredError(
                "the ingest pipeline was configured without external provider ownership"
            )

        self.provider_controller.install_initial(
            request,
            viewer_provider=viewer_provider_override,
            memory_extractor=memory_extractor_override,
        )
        viewer_provider = self.provider_router
        memory_extractor = self.provider_router
        owned_asr_provider = (
            StepFunAsrProvider(
                StepFunAsrConfig(
                    api_key=external_config.asr_api_key,
                    base_url=external_config.asr_base_url,
                    model=external_config.asr_model,
                ),
                ai_call_sink=self.debug_service,
            )
            if asr_provider_override is None
            else None
        )
        asr_provider = (
            owned_asr_provider if asr_provider_override is None else asr_provider_override
        )
        viewer_runtime = ViewerRuntime(
            provider=viewer_provider,
            barrage_pipeline=ViewerBarragePipeline(
                clock=self.clock,
                id_generator=self.id_generator,
            ),
            session_fence=self.runtime_state,
            publisher=RealtimeViewerBarragePublisher(self.realtime_broker),
            room_service=PersistentViewerRoomWriter(
                room_service=self.room_service,
                runtime_state=self.runtime_state,
                session_factory=self.database.session_factory,
            ),
            clock=self.clock,
            id_generator=self.id_generator,
            max_in_flight=12,
            trace_recorder=self.debug_service,
            behavior_state_sink=self.viewer_audience_service,
        )
        self.viewer_audience_service.bind_cancel_viewer(viewer_runtime.cancel_viewer)
        coordinator = ViewerRuntimeCoordinator(
            runtime_state=self.runtime_state,
            viewer_runtime=viewer_runtime,
            viewer_behavior=ViewerBehaviorService(),
            population_controller=self.viewer_audience_service,
            frame_metadata=StoredFrameMetadataResolver(
                frame_store=self.frame_store,
            ),
            memory_reader=self.shared_brain_service,
            visual_summarizer=viewer_provider,
            history_summarizer=viewer_provider,
            meme_sink=SharedBrainMemeCandidateSink(self.shared_brain_service),
            memory_extraction_sink=SharedBrainMemoryExtractionSink(
                extractor=memory_extractor,
                service=self.shared_brain_service,
            ),
        )
        self.debug_service.bind_runtime_agent(coordinator)
        scheduler = LatestWinsReactionScheduler(
            executor=coordinator,
            session_tasks=self.session_service,
            clock=self.clock,
            merge_window_provider=self.observation_merge_window_ms,
            failure_reporter=coordinator.record_reaction_failure,
        )
        ingest_service = IngestService(
            room_service=self.room_service,
            context_builder=self.context_builder,
            frame_store=self.frame_store,
            asr_provider=asr_provider,
            scheduler=scheduler,
            session_tasks=self.session_service,
            clock=self.clock,
            max_tracked_input_ids=self.pipeline_config.ingest_max_tracked_input_ids,
            voice_target_resolver=RuntimeTranscriptTargetResolver(self.runtime_state),
            ambient_enabled=self.ambient_enabled,
        )
        self.session_resources.add_resource(viewer_runtime)
        self.session_resources.add_resource(coordinator)
        self.session_resources.add_resource(ingest_service)
        self.ingest_gateway.configure(ingest_service)
        self.external_provider_config = external_config
        self._owned_asr_provider = owned_asr_provider
        self.viewer_runtime = viewer_runtime
        self.viewer_runtime_coordinator = coordinator
        self.reaction_scheduler = scheduler
        self.ingest_service = ingest_service
        return ingest_service

    def configure_recorded_runtime_pipeline(
        self,
        *,
        request: ProviderConfigurationRequest,
        viewer_provider: object,
        memory_extractor: RoomMemoryExtractor,
        asr_provider: AsrProvider,
    ) -> IngestService:
        """Configure the production graph with isolated deterministic adapters."""

        return self._configure_viewer_runtime_pipeline(
            request=request,
            external_config=ExternalProviderConfig(
                model_base_url=request.model_base_url,
                model_name=request.model_name,
                model_api_key=request.model_api_key,
                asr_api_key=request.asr_api_key,
            ),
            viewer_provider_override=viewer_provider,
            memory_extractor_override=memory_extractor,
            asr_provider_override=asr_provider,
        )

    @property
    def provider_configuration(self) -> ProviderConfigurationRequest | None:
        return self.provider_configuration_store.current()


def build_runtime(
    *,
    local_token: str | None = None,
    data_directory: str | Path | None = None,
    pipeline_config: PipelineConfig | None = None,
    runtime_capability_probe: RuntimeCapabilityProbe | None = None,
) -> BackendRuntime:
    token = create_local_token() if local_token is None else local_token
    if not token:
        raise ValueError("local_token must not be empty")

    resolved_data_directory = (
        Path(DEFAULT_DATA_DIRECTORY if data_directory is None else data_directory)
        .expanduser()
        .resolve()
    )
    active_pipeline_config = PipelineConfig() if pipeline_config is None else pipeline_config
    database = SQLiteDatabase(DatabaseConfig(data_directory=resolved_data_directory))
    unit_of_work_factory = SQLiteUnitOfWorkFactory(database.session_factory)
    session_record_store = SQLiteSessionRecordStore(unit_of_work_factory)
    broker = RealtimeBroker()
    clock = SystemClock()
    id_generator = UuidIdGenerator()
    runtime_state = RuntimeStateStore()
    room_event_store = PersistentRuntimeRoomEventStore(
        session_factory=database.session_factory,
        runtime_state=runtime_state,
        max_events=active_pipeline_config.room_event_capacity,
        event_ttl_ms=active_pipeline_config.room_event_ttl_ms,
    )
    room_service = RoomService(
        clock=clock,
        id_generator=id_generator,
        event_capacity=active_pipeline_config.room_event_capacity,
        event_ttl_ms=active_pipeline_config.room_event_ttl_ms,
        event_persister=room_event_store.persist,
    )
    context_builder = ContextBuilder(
        room_service=room_service,
        clock=clock,
        id_generator=id_generator,
        frame_capacity=active_pipeline_config.frame_capacity,
        frame_ttl_ms=active_pipeline_config.frame_ttl_ms,
        max_frames_per_observation=active_pipeline_config.max_frames_per_observation,
        max_events_per_observation=active_pipeline_config.max_events_per_observation,
    )
    audience_service = AudienceService(
        unit_of_work_factory=unit_of_work_factory,
        clock=clock,
        max_memories_per_audience=active_pipeline_config.audience_max_memories,
    )
    frame_store = InMemoryFrameStore(
        limits=FrameStoreLimits(
            max_frames=active_pipeline_config.frame_capacity,
            max_frame_bytes=active_pipeline_config.frame_max_bytes,
            max_total_bytes=active_pipeline_config.frame_total_bytes,
        ),
        id_generator=id_generator,
    )
    generation_trigger = DefaultGenerationTrigger(clock=clock)
    audience_selector = DefaultAudienceSelector()
    invocation_planner = DefaultGenerationInvocationPlanner()
    barrage_pipeline = BarragePipeline(
        policy=BarragePolicy(
            max_text_length=active_pipeline_config.barrage_max_text_length,
            ttl_ms=active_pipeline_config.barrage_ttl_ms,
            blocked_words=active_pipeline_config.barrage_blocked_words,
            duplicate_window_ms=active_pipeline_config.barrage_duplicate_window_ms,
            max_duplicate_entries_per_session=(
                active_pipeline_config.barrage_max_duplicate_entries
            ),
            density_window_ms=active_pipeline_config.barrage_density_window_ms,
            max_outputs_per_density_window=active_pipeline_config.barrage_max_outputs_per_window,
            max_tracked_sessions=active_pipeline_config.barrage_max_tracked_sessions,
        ),
        clock=clock,
        id_generator=id_generator,
    )
    session_resources = SessionResources(
        context_builder=context_builder,
        barrage_pipeline=barrage_pipeline,
        resources=(audience_service,),
    )
    ingest_gateway = IngestGateway()
    debug_service = DebugService(
        TraceStore(
            max_items=1_000,
            path=resolved_data_directory / "debug" / "viewer-traces.jsonl",
        ),
        runtime_state=runtime_state,
        ai_call_store=AiCallStore(
            max_items=1_000,
            path=resolved_data_directory / "debug" / "ai-calls.jsonl",
        ),
    )
    replay_service = ReplayService()
    shared_brain_service = SharedBrainService(
        session_factory=database.session_factory,
        runtime_state=runtime_state,
        clock=clock,
        room_service=room_service,
        room_event_store=room_event_store,
        observation_provenance=debug_service,
    )
    provider_configuration_store = ProviderConfigurationStore()
    provider_controller = RuntimeProviderController(
        frame_resolver=frame_store,
        configuration_committer=provider_configuration_store.set,
        ai_call_sink=debug_service,
    )
    provider_router = RuntimeProviderRouter(runtime_state)
    session_service = SessionService(
        clock=clock,
        id_generator=id_generator,
        publisher=broker,
        session_records=session_record_store,
        session_resources=session_resources,
        app_version=BACKEND_VERSION,
    )
    session_resources.add_resource(runtime_state)
    viewer_pool = ViewerPoolService(id_generator=id_generator)
    runtime_session_service = RuntimeSessionService(
        session_factory=database.session_factory,
        viewer_pool=viewer_pool,
        clock=clock,
        id_generator=id_generator,
        capability_probe=(
            ProductionRuntimeCapabilityProbe(
                configuration_provider=provider_configuration_store.current,
                asr_probe=create_stepfun_final_audio_probe(),
            )
            if runtime_capability_probe is None
            else runtime_capability_probe
        ),
        runtime_state=runtime_state,
        session_service=session_service,
        room_service=room_service,
        room_event_recovery=room_event_store,
        provider_controller=provider_controller,
        app_version=BACKEND_VERSION,
    )
    viewer_audience_service = ViewerAudienceService(
        runtime_state=runtime_state,
        session_factory=database.session_factory,
        broker=broker,
        clock=clock,
        viewer_pool=viewer_pool,
    )
    session_resources.add_resource(viewer_audience_service)
    return BackendRuntime(
        session_service=session_service,
        realtime_broker=broker,
        database=database,
        unit_of_work_factory=unit_of_work_factory,
        session_record_store=session_record_store,
        clock=clock,
        id_generator=id_generator,
        room_service=room_service,
        context_builder=context_builder,
        audience_service=audience_service,
        frame_store=frame_store,
        generation_trigger=generation_trigger,
        audience_selector=audience_selector,
        invocation_planner=invocation_planner,
        barrage_pipeline=barrage_pipeline,
        session_resources=session_resources,
        ingest_gateway=ingest_gateway,
        debug_service=debug_service,
        replay_service=replay_service,
        shared_brain_service=shared_brain_service,
        room_event_store=room_event_store,
        runtime_session_service=runtime_session_service,
        runtime_state=runtime_state,
        viewer_audience_service=viewer_audience_service,
        provider_configuration_store=provider_configuration_store,
        provider_controller=provider_controller,
        provider_router=provider_router,
        pipeline_config=active_pipeline_config,
        local_token=token,
    )


def build_runtime_from_environment() -> BackendRuntime:
    data_directory = os.environ.get(DATA_DIRECTORY_ENV)
    resolved_data_directory = (
        Path(DEFAULT_DATA_DIRECTORY if data_directory is None else data_directory)
        .expanduser()
        .resolve()
    )
    configure_logging(log_directory=resolved_data_directory / "logs")
    runtime = build_runtime(
        local_token=os.environ.get(LOCAL_TOKEN_ENV),
        data_directory=resolved_data_directory,
    )
    provider_values = {
        "model_base_url": os.environ.get(MODEL_BASE_URL_ENV),
        "model_name": os.environ.get(MODEL_NAME_ENV),
        "model_api_key": os.environ.get(MODEL_API_KEY_ENV),
        "asr_api_key": os.environ.get(ASR_API_KEY_ENV),
    }
    if any(value is not None for value in provider_values.values()):
        missing = [name for name, value in provider_values.items() if not value]
        if missing:
            raise ValueError(f"external provider environment is incomplete: {', '.join(missing)}")
        runtime.configure_provider_profile(
            ProviderConfigurationRequest(
                provider_profile_id="default",
                model_base_url=provider_values["model_base_url"] or "",
                model_name=provider_values["model_name"] or "",
                model_api_key=provider_values["model_api_key"] or "",
                asr_api_key=provider_values["asr_api_key"] or "",
            )
        )
    return runtime
