import os
from dataclasses import dataclass, field
from pathlib import Path

from advx_backend.application.barrage_pipeline import BarragePipeline
from advx_backend.application.context_builder import ContextBuilder
from advx_backend.application.generation_service import GenerationService
from advx_backend.application.ports.generation import (
    AudienceSelector,
    AudienceSnapshotProvider,
    GenerationInvocationPlanner,
    GenerationTrigger,
)
from advx_backend.application.ports.model import ModelProvider
from advx_backend.application.ports.persistence import UnitOfWorkFactory
from advx_backend.application.reaction_service import ReactionService
from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.room_service import RoomService
from advx_backend.application.session_resources import SessionResources
from advx_backend.application.session_service import SessionService
from advx_backend.domain.barrage import BarragePolicy
from advx_backend.infrastructure.persistence.sqlite import (
    DatabaseConfig,
    SQLiteDatabase,
    SQLiteSessionRecordStore,
    SQLiteUnitOfWorkFactory,
)
from advx_backend.infrastructure.security.local_token import create_local_token
from advx_backend.infrastructure.system import SystemClock, UuidIdGenerator

BACKEND_VERSION = "0.1.0"
LOCAL_TOKEN_ENV = "ADVX_LOCAL_TOKEN"
DATA_DIRECTORY_ENV = "ADVX_DATA_DIR"
DEFAULT_DATA_DIRECTORY = Path.cwd() / ".advx-data"


@dataclass(frozen=True)
class PipelineConfig:
    room_event_capacity: int = 256
    room_event_ttl_ms: int = 120_000
    frame_capacity: int = 8
    frame_ttl_ms: int = 10_000
    max_frames_per_observation: int = 3
    max_events_per_observation: int = 64
    barrage_max_text_length: int = 200
    barrage_ttl_ms: int = 15_000
    barrage_blocked_words: frozenset[str] = frozenset()
    barrage_duplicate_window_ms: int = 30_000
    barrage_max_duplicate_entries: int = 256
    barrage_density_window_ms: int = 10_000
    barrage_max_outputs_per_window: int = 6
    barrage_max_tracked_sessions: int = 2


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
    barrage_pipeline: BarragePipeline
    session_resources: SessionResources
    local_token: str = field(repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    async def startup(self) -> None:
        if self._started:
            return
        await self.database.start()
        await self.session_record_store.recover_interrupted(ended_at_ms=self.clock.now_ms())
        self._started = True

    async def shutdown(self) -> None:
        try:
            await self.session_service.shutdown()
        finally:
            await self.database.close()
            self._started = False

    def build_generation_service(
        self,
        *,
        snapshots: AudienceSnapshotProvider,
        trigger: GenerationTrigger,
        selector: AudienceSelector,
        invocation_planner: GenerationInvocationPlanner,
        model_provider: ModelProvider,
        max_concurrency: int = 4,
    ) -> GenerationService:
        return GenerationService(
            snapshots=snapshots,
            trigger=trigger,
            selector=selector,
            invocation_planner=invocation_planner,
            model_provider=model_provider,
            session_tasks=self.session_service,
            id_generator=self.id_generator,
            max_concurrency=max_concurrency,
        )

    def build_reaction_service(
        self,
        *,
        snapshots: AudienceSnapshotProvider,
        trigger: GenerationTrigger,
        selector: AudienceSelector,
        invocation_planner: GenerationInvocationPlanner,
        model_provider: ModelProvider,
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


def build_runtime(
    *,
    local_token: str | None = None,
    data_directory: str | Path | None = None,
    pipeline_config: PipelineConfig | None = None,
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
    room_service = RoomService(
        clock=clock,
        id_generator=id_generator,
        event_capacity=active_pipeline_config.room_event_capacity,
        event_ttl_ms=active_pipeline_config.room_event_ttl_ms,
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
    )
    session_service = SessionService(
        clock=clock,
        id_generator=id_generator,
        publisher=broker,
        session_records=session_record_store,
        session_resources=session_resources,
        app_version=BACKEND_VERSION,
    )
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
        barrage_pipeline=barrage_pipeline,
        session_resources=session_resources,
        local_token=token,
    )


def build_runtime_from_environment() -> BackendRuntime:
    return build_runtime(
        local_token=os.environ.get(LOCAL_TOKEN_ENV),
        data_directory=os.environ.get(DATA_DIRECTORY_ENV),
    )
