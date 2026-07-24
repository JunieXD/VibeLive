from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from advx_backend.application.ports.session import Clock, IdGenerator
from advx_backend.application.room_event_persistence import RoomEventRecoveryReader
from advx_backend.application.room_service import RoomService
from advx_backend.application.runtime_capability_probe import RuntimeCapabilityProbeError
from advx_backend.application.runtime_config_service import (
    RuntimeApplyError,
    RuntimeCapabilityProbe,
)
from advx_backend.application.runtime_provider import (
    RuntimeProviderController,
    RuntimeProviderGeneration,
)
from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.session_service import SessionService
from advx_backend.application.viewer_pool_service import (
    ViewerPoolReconciliation,
    ViewerPoolService,
    ViewerPoolSnapshot,
)
from advx_backend.contracts.configuration import RuntimeModelProviderCandidate
from advx_backend.contracts.session import (
    RuntimeSessionSnapshot,
    RuntimeSessionStartRequest,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    RuntimeApplyRequest,
    RuntimeDiffSummary,
    RuntimeRollbackRequest,
)
from advx_backend.domain.viewer import (
    ViewerInstance as DomainViewerInstance,
)
from advx_backend.domain.viewer import (
    ViewerInstanceVariant,
    ViewerLifecycleState,
    ViewerPrivateState,
)
from advx_backend.infrastructure.persistence.sqlite.models import (
    SessionRecordRow,
    SessionRuntimeRevisionRow,
    SessionViewerInstanceRow,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    RuntimePersistenceConflictError,
    RuntimeRevision,
    SQLiteRoomRepository,
    SQLiteSessionRuntimeRepository,
    SQLiteViewerInstanceRepository,
    canonical_json,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    ViewerInstance as PersistedViewerInstance,
)

logger = logging.getLogger(__name__)


def _log_capability_probe_failure(
    error: RuntimeCapabilityProbeError,
    *,
    operation: str,
    client_request_id: str | None = None,
    apply_id: str | None = None,
    provider_profile_id: str,
    session_id: str | None = None,
) -> None:
    logger.warning(
        "runtime.capability_probe.rejected",
        extra={
            "operation": operation,
            "client_request_id": client_request_id,
            "apply_id": apply_id,
            "provider_profile_id": provider_profile_id,
            "session_id": session_id,
            "capability_checks": [
                {
                    "capability": check.capability,
                    "status": check.status.value,
                    "model_id": check.model_id,
                    "error_code": check.error_code,
                    "http_status": check.http_status,
                }
                for check in error.checks
            ],
        },
    )


class RuntimeSessionError(RuntimeError):
    pass


class RuntimeSessionConflictError(RuntimeSessionError):
    pass


class RuntimeSessionNotFoundError(RuntimeSessionError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"runtime session {session_id} was not found")


class RuntimeSessionRecoveryError(RuntimeSessionError):
    pass


class NoOpRuntimeCapabilityProbe:
    async def probe(self, spec: CanonicalRuntimeSpec) -> None:
        del spec


class SessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


class RuntimeSessionService:
    """Coordinate canonical runtime snapshots with the durable v2 repositories."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | SessionFactory,
        viewer_pool: ViewerPoolService,
        clock: Clock,
        id_generator: IdGenerator,
        capability_probe: RuntimeCapabilityProbe | None = None,
        runtime_state: RuntimeStateStore | None = None,
        session_service: SessionService | None = None,
        room_service: RoomService | None = None,
        room_event_recovery: RoomEventRecoveryReader | None = None,
        provider_controller: RuntimeProviderController | None = None,
        app_version: str = "0.1.0",
    ) -> None:
        self._session_factory = session_factory
        self._viewer_pool = viewer_pool
        self._capability_probe = capability_probe or NoOpRuntimeCapabilityProbe()
        self._clock = clock
        self._id_generator = id_generator
        self._runtime_state = runtime_state
        self._session_service = session_service
        self._room_service = room_service
        self._room_event_recovery = room_event_recovery
        self._provider_controller = provider_controller
        self._app_version = app_version
        self._pools: dict[str, ViewerPoolSnapshot] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        request: RuntimeSessionStartRequest,
    ) -> RuntimeSessionSnapshot:
        async with self._lock:
            try:
                async with self._session_factory() as session:
                    runtime_repository = SQLiteSessionRuntimeRepository(session)
                    existing = await runtime_repository.get_idempotent_start(
                        request.client_request_id,
                        request_hash=request.client_config_hash,
                    )
                    if existing is not None:
                        return await self._snapshot(session, existing.session_id)
            except RuntimePersistenceConflictError as error:
                raise RuntimeSessionConflictError(str(error)) from error

            spec = request.canonical_runtime_spec
            try:
                await self._capability_probe.probe(spec)
            except RuntimeCapabilityProbeError as error:
                _log_capability_probe_failure(
                    error,
                    operation="start",
                    client_request_id=request.client_request_id,
                    provider_profile_id=spec.provider.provider_profile_id,
                )
                raise RuntimeApplyError(str(error)) from error
            except Exception as error:
                raise RuntimeApplyError(str(error)) from error
            provider_generation = self._current_provider_generation(spec)
            session_id = self._id_generator.new_id()
            apply_id = f"start:{request.client_request_id}"
            now_ms = self._clock.now_ms()
            pool = self._viewer_pool.create_pool(
                room_id=spec.room.room_id,
                session_id=session_id,
                audience_epoch=1,
                session_seed=session_id,
                spec=spec,
            )
            diff = RuntimeDiffSummary(
                added_viewer_ids=[
                    viewer.viewer_instance_id for viewer in pool.viewers
                ]
            )
            starting_persisted = False
            lifecycle_activated = False
            try:
                await self._persist_starting(
                    request=request,
                    session_id=session_id,
                    apply_id=apply_id,
                    diff=diff,
                    now_ms=now_ms,
                )
                starting_persisted = True
                if self._session_service is not None:
                    await self._session_service.activate_runtime_session(
                        session_id,
                        started_at_ms=now_ms,
                    )
                    lifecycle_activated = True

                async def persist() -> None:
                    await self._commit_started_runtime(
                        session_id=session_id,
                        pool=pool,
                        now_ms=now_ms,
                    )

                state = CommittedRuntime(
                    session_id=session_id,
                    spec=spec,
                    audience_epoch=pool.audience_epoch,
                    pool=pool,
                    population_revision=1,
                    provider_generation=provider_generation,
                )
                if self._runtime_state is None:
                    await persist()
                else:
                    await self._runtime_state.activate_after(state, persist)
            except RuntimePersistenceConflictError as error:
                if lifecycle_activated and self._session_service is not None:
                    await self._session_service.abandon_runtime_session(session_id)
                if starting_persisted:
                    await self._reject_start(
                        session_id,
                        now_ms=self._clock.now_ms(),
                    )
                raise RuntimeSessionConflictError(str(error)) from error
            except BaseException:
                if lifecycle_activated and self._session_service is not None:
                    await asyncio.shield(
                        self._session_service.abandon_runtime_session(session_id)
                    )
                if starting_persisted:
                    await asyncio.shield(
                        self._reject_start(
                            session_id,
                            now_ms=self._clock.now_ms(),
                        )
                    )
                raise

            self._pools[session_id] = pool
            return _response(
                spec,
                pool,
                recovered=False,
                apply_id=apply_id,
                diff=diff,
            )

    async def current(self, session_id: str) -> RuntimeSessionSnapshot:
        async with self._lock:
            async with self._session_factory() as session:
                return await self._snapshot(session, session_id)

    async def apply(
        self,
        session_id: str,
        request: RuntimeApplyRequest,
    ) -> RuntimeSessionSnapshot:
        async with self._lock:
            async with self._session_factory() as session:
                current = await self._snapshot(session, session_id)
                current_storage_revision = await self._storage_revision(
                    session, session_id
                )
                prior = await self._revision_for_apply(
                    session, session_id, request.apply_id
                )
                if prior is not None:
                    if (
                        prior.config_hash != request.client_config_hash
                        or CanonicalRuntimeSpec.model_validate_json(
                            prior.canonical_spec_json
                        )
                        != request.canonical_runtime_spec
                    ):
                        raise RuntimeSessionConflictError(
                            "apply_id was already used with different revision content"
                        )
                    if prior.status == "committed":
                        return current
                    if prior.status == "rejected":
                        raise ValueError("runtime apply was already rejected")

                if request.base_revision != current.config_revision:
                    raise ValueError(
                        f"base revision {request.base_revision} is not current"
                    )
                if (
                    request.canonical_runtime_spec.room.room_id
                    != current.room_id
                ):
                    raise ValueError("runtime spec belongs to a different Room")
                if (
                    request.canonical_runtime_spec.config_revision
                    <= request.base_revision
                ):
                    raise ValueError("config revision must advance")

                pending_revision = current_storage_revision + 1
                if prior is None:
                    await SQLiteSessionRuntimeRepository(
                        session
                    ).add_pending_revision(
                        RuntimeRevision(
                            session_id=session_id,
                            revision=pending_revision,
                            apply_id=request.apply_id,
                            base_revision=current_storage_revision,
                            config_hash=request.client_config_hash,
                            status="pending",
                            canonical_spec_json=(
                                request.canonical_runtime_spec.canonical_json()
                            ),
                            diff_summary_json=canonical_json({}),
                            created_at_ms=self._clock.now_ms(),
                            updated_at_ms=self._clock.now_ms(),
                        )
                    )
                    await session.commit()
                else:
                    pending_revision = prior.revision

            current_pool, current_population_revision = await self._audience_state(
                session_id
            )
            candidate_generation: RuntimeProviderGeneration | None = None
            try:
                candidate_generation = await self._prepare_provider_generation(
                    current_spec=current.canonical_runtime_spec,
                    next_spec=request.canonical_runtime_spec,
                    candidate=request.provider_candidate,
                )
                reconciliation = self._viewer_pool.reconcile(
                    current=current_pool,
                    next_epoch=current.audience_epoch + 1,
                    spec=request.canonical_runtime_spec,
                )
            except Exception as error:
                if candidate_generation is not None:
                    await asyncio.shield(candidate_generation.retire())
                async with self._session_factory() as session:
                    await SQLiteSessionRuntimeRepository(session).reject_revision(
                        session_id,
                        pending_revision,
                        now_ms=self._clock.now_ms(),
                    )
                    await session.commit()
                raise RuntimeApplyError(str(error)) from error

            async def persist() -> None:
                await self._commit_reconciliation(
                    session_id=session_id,
                    storage_revision=pending_revision,
                    expected_storage_revision=current_storage_revision,
                    reconciliation=reconciliation,
                    expected_population_revision=current_population_revision,
                    next_population_revision=current_population_revision + 1,
                    target_concurrent_viewers=self._target_concurrent_viewers(
                        request.canonical_runtime_spec
                    ),
                )
            state = CommittedRuntime(
                session_id=session_id,
                spec=request.canonical_runtime_spec,
                audience_epoch=reconciliation.snapshot.audience_epoch,
                pool=reconciliation.snapshot,
                population_revision=current_population_revision + 1,
                provider_generation=(
                    candidate_generation
                    if candidate_generation is not None
                    else await self._bound_provider_generation(session_id)
                ),
            )
            try:
                if self._runtime_state is None:
                    await persist()
                else:
                    await self._runtime_state.replace_after(state, persist)
            except BaseException:
                if candidate_generation is not None:
                    await asyncio.shield(candidate_generation.retire())
                raise
            if candidate_generation is not None:
                await asyncio.shield(
                    self._commit_provider_generation(candidate_generation)
                )
            self._pools[session_id] = reconciliation.snapshot
            return _response(
                request.canonical_runtime_spec,
                reconciliation.snapshot,
                recovered=False,
                apply_id=request.apply_id,
                diff=_reconciliation_diff(reconciliation),
            )

    async def rollback(
        self,
        session_id: str,
        request: RuntimeRollbackRequest,
    ) -> RuntimeSessionSnapshot:
        async with self._lock:
            async with self._session_factory() as session:
                current = await self._snapshot(session, session_id)
                prior = await self._revision_for_apply(
                    session, session_id, request.apply_id
                )
                if prior is not None:
                    target_spec = await self._validate_prior_rollback(
                        session,
                        session_id=session_id,
                        prior=prior,
                        request=request,
                    )
                    if prior.status == "committed":
                        return current
                    if prior.status == "rejected":
                        raise ValueError("runtime rollback was already rejected")
                    storage_revision = await self._storage_revision(session, session_id)
                    if prior.base_revision != storage_revision:
                        raise RuntimeSessionConflictError(
                            "apply_id was already used with different rollback content"
                        )
                    next_storage_revision = prior.revision
                else:
                    if request.base_revision != current.config_revision:
                        raise ValueError(
                            f"base revision {request.base_revision} is not current"
                        )
                    target = await self._committed_config_revision(
                        session, session_id, request.target_revision
                    )
                    if target is None:
                        raise ValueError(
                            f"target revision {request.target_revision} was not found"
                        )
                    target_spec = CanonicalRuntimeSpec.model_validate_json(
                        target.canonical_spec_json
                    )
                    rollback_identity = self._rollback_identity(
                        request,
                        target_spec=target_spec,
                    )
                    storage_revision = await self._storage_revision(session, session_id)
                    next_storage_revision = storage_revision + 1
                    await SQLiteSessionRuntimeRepository(
                        session
                    ).add_pending_revision(
                        RuntimeRevision(
                            session_id=session_id,
                            revision=next_storage_revision,
                            apply_id=request.apply_id,
                            base_revision=storage_revision,
                            config_hash=target.config_hash,
                            status="pending",
                            canonical_spec_json=target.canonical_spec_json,
                            diff_summary_json=canonical_json(
                                {"_rollback_identity": rollback_identity}
                            ),
                            created_at_ms=self._clock.now_ms(),
                            updated_at_ms=self._clock.now_ms(),
                        )
                    )
                    await session.commit()

            current_pool, current_population_revision = await self._audience_state(
                session_id
            )
            candidate_generation: RuntimeProviderGeneration | None = None
            try:
                candidate_generation = await self._prepare_provider_generation(
                    current_spec=current.canonical_runtime_spec,
                    next_spec=target_spec,
                    candidate=request.provider_candidate,
                )
                reconciliation = self._viewer_pool.reconcile(
                    current=current_pool,
                    next_epoch=current.audience_epoch + 1,
                    spec=target_spec,
                )
            except Exception as error:
                if candidate_generation is not None:
                    await asyncio.shield(candidate_generation.retire())
                async with self._session_factory() as session:
                    await SQLiteSessionRuntimeRepository(session).reject_revision(
                        session_id,
                        next_storage_revision,
                        now_ms=self._clock.now_ms(),
                    )
                    await session.commit()
                raise RuntimeApplyError(str(error)) from error
            async def persist() -> None:
                await self._commit_reconciliation(
                    session_id=session_id,
                    storage_revision=next_storage_revision,
                    expected_storage_revision=storage_revision,
                    reconciliation=reconciliation,
                    expected_population_revision=current_population_revision,
                    next_population_revision=current_population_revision + 1,
                    target_concurrent_viewers=self._target_concurrent_viewers(
                        target_spec
                    ),
                    revision_metadata={
                        "_rollback_identity": self._rollback_identity(
                            request,
                            target_spec=target_spec,
                        )
                    },
                )
            state = CommittedRuntime(
                session_id=session_id,
                spec=target_spec,
                audience_epoch=reconciliation.snapshot.audience_epoch,
                pool=reconciliation.snapshot,
                population_revision=current_population_revision + 1,
                provider_generation=(
                    candidate_generation
                    if candidate_generation is not None
                    else await self._bound_provider_generation(session_id)
                ),
            )
            try:
                if self._runtime_state is None:
                    await persist()
                else:
                    await self._runtime_state.replace_after(state, persist)
            except BaseException:
                if candidate_generation is not None:
                    await asyncio.shield(candidate_generation.retire())
                raise
            if candidate_generation is not None:
                await asyncio.shield(
                    self._commit_provider_generation(candidate_generation)
                )
            self._pools[session_id] = reconciliation.snapshot
            return _response(
                target_spec,
                reconciliation.snapshot,
                recovered=False,
                apply_id=request.apply_id,
                diff=_reconciliation_diff(reconciliation),
            )

    async def recover(self, session_id: str) -> RuntimeSessionSnapshot:
        async with self._lock:
            lifecycle_activated = False
            recovery_revision: int | None = None
            recovered_events = ()
            try:
                async with self._session_factory() as session:
                    current = await self._snapshot(session, session_id)
                    current_storage_revision = await self._storage_revision(
                        session, session_id
                    )
                    committed = await SQLiteSessionRuntimeRepository(
                        session
                    ).committed_revision(session_id)
                    if committed is None:
                        raise RuntimeSessionRecoveryError(
                            "a committed runtime snapshot is required for recovery"
                        )
                    spec = CanonicalRuntimeSpec.model_validate_json(
                        committed.canonical_spec_json
                    )
                    record = await session.get(SessionRecordRow, session_id)
                    if record is None:
                        raise RuntimeSessionNotFoundError(session_id)
                    if (
                        record.outcome != "interrupted"
                        or record.ended_at_ms is None
                    ):
                        raise RuntimeSessionRecoveryError(
                            "only an interrupted runtime Session can be recovered"
                        )
                    if self._room_event_recovery is not None:
                        recovered_events = await self._room_event_recovery.load_for_recovery(
                            room_id=spec.room.room_id,
                            session_id=session_id,
                            maximum_audience_epoch=current.audience_epoch,
                        )
                    if self._session_service is not None:
                        lifecycle = await self._session_service.status()
                        lifecycle_activated = lifecycle.session_id is None
                        await self._session_service.activate_runtime_session(
                            session_id,
                            started_at_ms=record.started_at_ms,
                        )
                    if (
                        self._room_service is not None
                        and self._room_event_recovery is not None
                    ):
                        await self._room_service.restore_events(
                            session_id,
                            recovered_events,
                        )
                    apply_id = f"recover:{self._id_generator.new_id()}"
                    now_ms = self._clock.now_ms()
                    runtime_repository = SQLiteSessionRuntimeRepository(session)
                    latest_storage_revision = (
                        await runtime_repository.reject_orphaned_pending_revisions(
                            session_id,
                            now_ms=now_ms,
                        )
                    )
                    recovery_revision = latest_storage_revision + 1
                    await runtime_repository.add_pending_revision(
                        RuntimeRevision(
                            session_id=session_id,
                            revision=recovery_revision,
                            apply_id=apply_id,
                            base_revision=current_storage_revision,
                            config_hash=committed.config_hash,
                            status="pending",
                            canonical_spec_json=committed.canonical_spec_json,
                            diff_summary_json=canonical_json({"recovered": True}),
                            created_at_ms=now_ms,
                            updated_at_ms=now_ms,
                        )
                    )
                    await session.commit()

                current_pool, current_population_revision = await self._audience_state(
                    session_id
                )
                reconciliation = self._viewer_pool.reconcile(
                    current=current_pool,
                    next_epoch=current.audience_epoch + 1,
                    spec=spec,
                )

                async def persist() -> None:
                    assert recovery_revision is not None
                    await self._commit_reconciliation(
                        session_id=session_id,
                        storage_revision=recovery_revision,
                        expected_storage_revision=current_storage_revision,
                        reconciliation=reconciliation,
                        expected_population_revision=current_population_revision,
                        next_population_revision=current_population_revision + 1,
                        target_concurrent_viewers=self._target_concurrent_viewers(spec),
                        recovery={
                            "recovered": True,
                            "recovered_at_ms": now_ms,
                        },
                    )

                state = CommittedRuntime(
                    session_id=session_id,
                    spec=spec,
                    audience_epoch=reconciliation.snapshot.audience_epoch,
                    pool=reconciliation.snapshot,
                    population_revision=current_population_revision + 1,
                    provider_generation=await self._bound_provider_generation(
                        session_id
                    ),
                )
                if self._runtime_state is None:
                    await persist()
                else:
                    await self._runtime_state.replace_after(state, persist)
            except BaseException:
                if recovery_revision is not None:
                    await asyncio.shield(
                        self._reject_revision(
                            session_id,
                            recovery_revision,
                            now_ms=self._clock.now_ms(),
                        )
                    )
                if lifecycle_activated and self._session_service is not None:
                    await asyncio.shield(
                        self._session_service.abandon_runtime_session(
                            session_id
                        )
                    )
                raise

            self._pools[session_id] = reconciliation.snapshot
            return _response(
                spec,
                reconciliation.snapshot,
                recovered=True,
                apply_id=apply_id,
                diff=_reconciliation_diff(reconciliation),
            )

    async def _reject_revision(
        self,
        session_id: str,
        revision: int,
        *,
        now_ms: int,
    ) -> None:
        async with self._session_factory() as session:
            try:
                await SQLiteSessionRuntimeRepository(session).reject_revision(
                    session_id,
                    revision,
                    now_ms=now_ms,
                )
            except RuntimePersistenceConflictError:
                return
            await session.commit()

    async def _persist_starting(
        self,
        *,
        request: RuntimeSessionStartRequest,
        session_id: str,
        apply_id: str,
        diff: RuntimeDiffSummary,
        now_ms: int,
    ) -> None:
        spec = request.canonical_runtime_spec
        async with self._session_factory() as session:
            room_repository = SQLiteRoomRepository(session)
            await room_repository.get_or_create(
                spec.room.room_id,
                display_name=spec.room.display_name,
                now_ms=now_ms,
            )
            runtime_repository = SQLiteSessionRuntimeRepository(session)
            record, created = await runtime_repository.start(
                session_id=session_id,
                room_id=spec.room.room_id,
                client_request_id=request.client_request_id,
                request_hash=request.client_config_hash,
                apply_id=apply_id,
                canonical_spec_json=spec.canonical_json(),
                diff_summary_json=canonical_json(diff.model_dump(mode="json")),
                app_version=self._app_version,
                session_seed=session_id,
                target_concurrent_viewers=next(
                    mode.target_concurrent_viewers
                    for mode in spec.modes
                    if mode.mode_id == spec.active_mode_id
                ),
                now_ms=now_ms,
            )
            if not created:
                raise RuntimePersistenceConflictError(
                    "concurrent runtime start resolved to "
                    f"existing session {record.session_id}"
                )
            await session.commit()

    async def _commit_started_runtime(
        self,
        *,
        session_id: str,
        pool: ViewerPoolSnapshot,
        now_ms: int,
    ) -> None:
        async with self._session_factory() as session:
            await SQLiteViewerInstanceRepository(session).add_all(
                [_persisted_viewer(viewer) for viewer in pool.viewers]
            )
            await SQLiteSessionRuntimeRepository(session).commit_revision(
                session_id,
                1,
                expected_base_revision=0,
                next_epoch=1,
                expected_population_revision=1,
                next_population_revision=1,
                target_concurrent_viewers=len(pool.viewers),
                next_creation_ordinal=(
                    max((viewer.ordinal for viewer in pool.viewers), default=0) + 1
                ),
                now_ms=now_ms,
            )
            await session.commit()

    async def _reject_start(self, session_id: str, *, now_ms: int) -> None:
        async with self._session_factory() as session:
            runtime_repository = SQLiteSessionRuntimeRepository(session)
            try:
                await runtime_repository.reject_revision(
                    session_id,
                    1,
                    now_ms=now_ms,
                )
            except RuntimePersistenceConflictError:
                pass
            await session.execute(
                update(SessionRecordRow)
                .where(
                    SessionRecordRow.session_id == session_id,
                    SessionRecordRow.ended_at_ms.is_(None),
                )
                .values(
                    state="failed",
                    ended_at_ms=now_ms,
                    outcome="error",
                )
            )
            await session.commit()

    async def _snapshot(
        self,
        session: AsyncSession,
        session_id: str,
    ) -> RuntimeSessionSnapshot:
        record = await session.get(SessionRecordRow, session_id)
        if record is None:
            raise RuntimeSessionNotFoundError(session_id)
        committed = await SQLiteSessionRuntimeRepository(
            session
        ).committed_revision(session_id)
        if committed is None or record.audience_epoch is None:
            raise RuntimeSessionRecoveryError(
                "the runtime session has no committed snapshot"
            )
        spec = CanonicalRuntimeSpec.model_validate_json(
            committed.canonical_spec_json
        )
        pool = self._pools.get(session_id)
        existing_state: CommittedRuntime | None = None
        if self._runtime_state is not None:
            try:
                existing_state = await self._runtime_state.snapshot(session_id)
            except KeyError:
                pass
            else:
                if (
                    existing_state.audience_epoch == record.audience_epoch
                    and existing_state.spec == spec
                ):
                    pool = existing_state.pool
        if pool is None or pool.audience_epoch != record.audience_epoch:
            pool = await self._load_pool(
                session,
                spec=spec,
                session_id=session_id,
                audience_epoch=record.audience_epoch,
                session_seed=record.session_seed or session_id,
                next_creation_ordinal=record.next_creation_ordinal,
            )
        self._pools[session_id] = pool
        active_record = record.ended_at_ms is None and record.state in {
            "starting",
            "running",
            "paused",
        }
        if active_record:
            if self._session_service is not None:
                await self._session_service.activate_runtime_session(
                    session_id,
                    started_at_ms=record.started_at_ms,
                )
        if self._runtime_state is not None:
            state = CommittedRuntime(
                session_id=session_id,
                spec=spec,
                audience_epoch=pool.audience_epoch,
                pool=pool,
                population_revision=record.population_revision,
                provider_generation=self._current_provider_generation(spec),
                accepting_results=active_record,
            )
            if existing_state is None:
                await self._runtime_state.activate(state)
            elif (
                existing_state.audience_epoch != state.audience_epoch
                or existing_state.spec != state.spec
                or existing_state.population_revision != state.population_revision
            ) and existing_state.accepting_results:
                raise RuntimeSessionConflictError(
                    "persisted runtime snapshot diverged from active runtime state"
                )
        try:
            diff_payload = json.loads(committed.diff_summary_json)
            if not isinstance(diff_payload, dict):
                raise ValueError
            diff_payload.pop("_rollback_identity", None)
            diff = RuntimeDiffSummary.model_validate(diff_payload)
        except (json.JSONDecodeError, ValueError):
            diff = RuntimeDiffSummary()
        return _response(
            spec,
            pool,
            recovered=_is_recovered(record.recovery_json),
            apply_id=committed.apply_id,
            diff=diff,
        )

    async def _load_pool(
        self,
        session: AsyncSession,
        *,
        spec: CanonicalRuntimeSpec,
        session_id: str,
        audience_epoch: int,
        session_seed: str,
        next_creation_ordinal: int,
    ) -> ViewerPoolSnapshot:
        rows = list(
            await session.scalars(
                select(SessionViewerInstanceRow)
                .where(
                    SessionViewerInstanceRow.session_id == session_id,
                    SessionViewerInstanceRow.presence_state != "removed",
                )
                .order_by(
                    SessionViewerInstanceRow.persona_id,
                    SessionViewerInstanceRow.ordinal,
                )
            )
        )
        if not rows:
            return self._viewer_pool.create_pool(
                room_id=spec.room.room_id,
                session_id=session_id,
                audience_epoch=audience_epoch,
                session_seed=session_seed,
                spec=spec,
            )
        viewers = [
            DomainViewerInstance(
                viewer_instance_id=row.viewer_instance_id,
                room_id=spec.room.room_id,
                session_id=session_id,
                audience_epoch=audience_epoch,
                persona_id=row.persona_id,
                persona_revision=row.persona_revision,
                persona_content_hash=row.persona_content_hash,
                ordinal=row.ordinal,
                username=row.username or row.display_name,
                display_name=row.display_name,
                avatar_seed=row.avatar_seed or row.viewer_instance_id,
                color_seed=row.color_seed or row.viewer_instance_id,
                locale=row.locale,
                variant=ViewerInstanceVariant.model_validate_json(
                    row.micro_variant_json
                ),
                private_state=ViewerPrivateState.model_validate_json(
                    row.behavior_state_json
                ),
                viewer_sequence=row.viewer_sequence,
                lifecycle_state=ViewerLifecycleState(row.presence_state),
                presence_revision=row.presence_revision,
                moderation_revision=row.moderation_revision,
                behavior_revision=row.behavior_revision,
                joined_at_ms=row.joined_at_ms,
                last_left_at_ms=row.last_left_at_ms,
                join_count=row.join_count,
                muted_until_ms=row.muted_until_ms,
                mute_reason=row.mute_reason,
                kicked_at_ms=row.kicked_at_ms,
                kick_reason=row.kick_reason,
                created_at_ms=row.created_at_ms,
                removed_at_ms=(
                    row.kicked_at_ms
                    if row.presence_state == ViewerLifecycleState.KICKED.value
                    else None
                ),
            )
            for row in rows
        ]
        return ViewerPoolSnapshot(
            room_id=spec.room.room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            mode_id=spec.active_mode_id,
            session_seed=session_seed,
            next_creation_ordinal=max(
                next_creation_ordinal,
                max((viewer.ordinal for viewer in viewers), default=0) + 1,
            ),
            viewers=viewers,
        )

    async def _audience_state(
        self,
        session_id: str,
    ) -> tuple[ViewerPoolSnapshot, int]:
        if self._runtime_state is not None:
            state = await self._runtime_state.snapshot(session_id)
            self._pools[session_id] = state.pool
            return state.pool, state.population_revision
        async with self._session_factory() as session:
            record = await session.get(SessionRecordRow, session_id)
            if record is None:
                raise RuntimeSessionNotFoundError(session_id)
            return self._pools[session_id], record.population_revision

    @staticmethod
    def _target_concurrent_viewers(spec: CanonicalRuntimeSpec) -> int:
        return next(
            mode.target_concurrent_viewers
            for mode in spec.modes
            if mode.mode_id == spec.active_mode_id
        )

    def _current_provider_generation(
        self,
        spec: CanonicalRuntimeSpec,
    ) -> RuntimeProviderGeneration | None:
        if self._provider_controller is None:
            return None
        return self._provider_controller.current_for(spec.provider)

    async def _bound_provider_generation(
        self,
        session_id: str,
    ) -> RuntimeProviderGeneration | None:
        if self._runtime_state is None:
            return None
        return (await self._runtime_state.snapshot(session_id)).provider_generation

    async def _prepare_provider_generation(
        self,
        *,
        current_spec: CanonicalRuntimeSpec,
        next_spec: CanonicalRuntimeSpec,
        candidate: RuntimeModelProviderCandidate | None,
    ) -> RuntimeProviderGeneration | None:
        provider_changed = current_spec.provider != next_spec.provider
        if not provider_changed:
            if candidate is not None:
                raise ValueError(
                    "provider_candidate is only valid when the provider spec changes"
                )
            return None
        if candidate is None:
            raise ValueError("provider_candidate is required when the provider spec changes")
        if self._provider_controller is None:
            raise RuntimeError("runtime provider hot swap is not configured")
        probe_candidate = getattr(self._capability_probe, "probe_candidate", None)
        if probe_candidate is None:
            raise RuntimeError("runtime provider candidate probing is not configured")
        await probe_candidate(next_spec, candidate)
        return self._provider_controller.build(next_spec.provider, candidate)

    async def _commit_provider_generation(
        self,
        generation: RuntimeProviderGeneration,
    ) -> None:
        assert self._provider_controller is not None
        previous = self._provider_controller.commit(generation)
        if previous is not None and previous is not generation:
            await previous.retire()

    async def _commit_reconciliation(
        self,
        *,
        session_id: str,
        storage_revision: int,
        expected_storage_revision: int,
        reconciliation: ViewerPoolReconciliation,
        expected_population_revision: int,
        next_population_revision: int,
        target_concurrent_viewers: int,
        recovery: dict[str, object] | None = None,
        revision_metadata: dict[str, object] | None = None,
    ) -> None:
        next_creation_ordinal = reconciliation.snapshot.next_creation_ordinal
        now_ms = self._clock.now_ms()
        diff = RuntimeDiffSummary(
            added_viewer_ids=list(reconciliation.added_viewer_ids),
            retained_viewer_ids=list(reconciliation.retained_viewer_ids),
            reset_viewer_ids=list(reconciliation.reset_viewer_ids),
            removed_viewer_ids=list(reconciliation.removed_viewer_ids),
        )
        diff_payload = diff.model_dump(mode="json")
        if revision_metadata is not None:
            diff_payload.update(revision_metadata)
        async with self._session_factory() as session:
            persisted_viewers = SQLiteViewerInstanceRepository(session)
            for viewer_id in reconciliation.removed_viewer_ids:
                await persisted_viewers.remove(
                    session_id,
                    viewer_id,
                    removed_epoch=reconciliation.snapshot.audience_epoch,
                )
            added = {
                viewer.viewer_instance_id
                for viewer in reconciliation.snapshot.viewers
                if viewer.viewer_instance_id in reconciliation.added_viewer_ids
            }
            if added:
                await persisted_viewers.add_all(
                    [
                        _persisted_viewer(viewer)
                        for viewer in reconciliation.snapshot.viewers
                        if viewer.viewer_instance_id in added
                    ]
                )
            for viewer in reconciliation.snapshot.viewers:
                if viewer.viewer_instance_id in added:
                    continue
                await session.execute(
                    update(SessionViewerInstanceRow)
                    .where(
                        SessionViewerInstanceRow.session_id == session_id,
                        SessionViewerInstanceRow.viewer_instance_id
                        == viewer.viewer_instance_id,
                    )
                    .values(
                        persona_id=viewer.persona_id,
                        persona_revision=viewer.persona_revision,
                        persona_content_hash=viewer.persona_content_hash,
                        display_name=viewer.display_name,
                        micro_variant_json=canonical_json(
                            viewer.variant.model_dump(mode="json")
                        ),
                        presence_state=viewer.lifecycle_state.value,
                        presence_revision=viewer.presence_revision,
                        moderation_revision=viewer.moderation_revision,
                        behavior_revision=viewer.behavior_revision,
                        joined_at_ms=viewer.joined_at_ms,
                        last_left_at_ms=viewer.last_left_at_ms,
                        join_count=viewer.join_count,
                        muted_until_ms=viewer.muted_until_ms,
                        mute_reason=viewer.mute_reason,
                        kicked_at_ms=viewer.kicked_at_ms,
                        kick_reason=viewer.kick_reason,
                        viewer_sequence=viewer.viewer_sequence,
                        behavior_state_json=canonical_json(
                            viewer.private_state.model_dump(mode="json")
                        ),
                        updated_at_ms=now_ms,
                    )
                )
            await session.execute(
                update(SessionRuntimeRevisionRow)
                .where(
                    SessionRuntimeRevisionRow.session_id == session_id,
                    SessionRuntimeRevisionRow.revision == storage_revision,
                    SessionRuntimeRevisionRow.status == "pending",
                )
                .values(
                    diff_summary_json=canonical_json(diff_payload)
                )
            )
            if recovery is not None:
                await session.execute(
                    update(SessionRecordRow)
                    .where(SessionRecordRow.session_id == session_id)
                    .values(
                        state="starting",
                        ended_at_ms=None,
                        outcome=None,
                    )
                )
            await SQLiteSessionRuntimeRepository(session).commit_revision(
                session_id,
                storage_revision,
                expected_base_revision=expected_storage_revision,
                next_epoch=reconciliation.snapshot.audience_epoch,
                expected_population_revision=expected_population_revision,
                next_population_revision=next_population_revision,
                target_concurrent_viewers=target_concurrent_viewers,
                next_creation_ordinal=next_creation_ordinal,
                now_ms=now_ms,
                recovery=recovery,
            )
            await session.commit()

    @staticmethod
    async def _storage_revision(
        session: AsyncSession,
        session_id: str,
    ) -> int:
        revision = await session.scalar(
            select(SessionRuntimeRevisionRow.revision)
            .where(
                SessionRuntimeRevisionRow.session_id == session_id,
                SessionRuntimeRevisionRow.status == "committed",
            )
            .order_by(SessionRuntimeRevisionRow.revision.desc())
            .limit(1)
        )
        if revision is None:
            raise RuntimeSessionRecoveryError(
                "the runtime session has no committed snapshot"
            )
        return revision

    @staticmethod
    async def _revision_for_apply(
        session: AsyncSession,
        session_id: str,
        apply_id: str,
    ) -> RuntimeRevision | None:
        row = await session.scalar(
            select(SessionRuntimeRevisionRow).where(
                SessionRuntimeRevisionRow.session_id == session_id,
                SessionRuntimeRevisionRow.apply_id == apply_id,
            )
        )
        if row is None:
            return None
        return RuntimeRevision(
            session_id=row.session_id,
            revision=row.revision,
            apply_id=row.apply_id,
            base_revision=row.base_revision,
            config_hash=row.config_hash,
            status=row.status,
            canonical_spec_json=row.canonical_spec_json,
            diff_summary_json=row.diff_summary_json,
            created_at_ms=row.created_at_ms,
            updated_at_ms=row.updated_at_ms,
        )

    @staticmethod
    async def _validate_prior_rollback(
        session: AsyncSession,
        *,
        session_id: str,
        prior: RuntimeRevision,
        request: RuntimeRollbackRequest,
    ) -> CanonicalRuntimeSpec:
        try:
            target_spec = CanonicalRuntimeSpec.model_validate_json(
                prior.canonical_spec_json
            )
        except ValueError as error:
            raise RuntimeSessionConflictError(
                "apply_id was already used with different rollback content"
            ) from error
        try:
            diff_payload = json.loads(prior.diff_summary_json)
        except json.JSONDecodeError as error:
            raise RuntimeSessionConflictError(
                "apply_id was already used with different rollback content"
            ) from error
        if not isinstance(diff_payload, dict):
            raise RuntimeSessionConflictError(
                "apply_id was already used with different rollback content"
            )
        stored_identity = diff_payload.get("_rollback_identity")
        expected_identity = RuntimeSessionService._rollback_identity(
            request,
            target_spec=target_spec,
        )
        if stored_identity is not None and stored_identity != expected_identity:
            raise RuntimeSessionConflictError(
                "apply_id was already used with different rollback content"
            )
        base_row = await session.scalar(
            select(SessionRuntimeRevisionRow).where(
                SessionRuntimeRevisionRow.session_id == session_id,
                SessionRuntimeRevisionRow.revision == prior.base_revision,
                SessionRuntimeRevisionRow.status == "committed",
            )
        )
        if base_row is None:
            raise RuntimeSessionConflictError(
                "apply_id was already used with different rollback content"
            )
        try:
            base_spec = CanonicalRuntimeSpec.model_validate_json(
                base_row.canonical_spec_json
            )
        except ValueError as error:
            raise RuntimeSessionConflictError(
                "apply_id was already used with different rollback content"
            ) from error
        if (
            target_spec.config_revision != request.target_revision
            or base_spec.config_revision != request.base_revision
            or prior.config_hash != target_spec.config_hash()
        ):
            raise RuntimeSessionConflictError(
                "apply_id was already used with different rollback content"
            )
        return target_spec

    @staticmethod
    def _rollback_identity(
        request: RuntimeRollbackRequest,
        *,
        target_spec: CanonicalRuntimeSpec,
    ) -> dict[str, object]:
        candidate_fingerprint: str | None = None
        if request.provider_candidate is not None:
            candidate_json = json.dumps(
                request.provider_candidate.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            candidate_fingerprint = hashlib.sha256(
                candidate_json.encode("utf-8")
            ).hexdigest()
        return {
            "base_revision": request.base_revision,
            "target_revision": request.target_revision,
            "target_config_hash": target_spec.config_hash(),
            "provider_candidate_fingerprint": candidate_fingerprint,
        }

    @staticmethod
    async def _committed_config_revision(
        session: AsyncSession,
        session_id: str,
        config_revision: int,
    ) -> RuntimeRevision | None:
        rows = list(
            await session.scalars(
                select(SessionRuntimeRevisionRow)
                .where(
                    SessionRuntimeRevisionRow.session_id == session_id,
                    SessionRuntimeRevisionRow.status == "committed",
                )
                .order_by(SessionRuntimeRevisionRow.revision.desc())
            )
        )
        for row in rows:
            spec = CanonicalRuntimeSpec.model_validate_json(
                row.canonical_spec_json
            )
            if spec.config_revision == config_revision:
                return RuntimeRevision(
                    session_id=row.session_id,
                    revision=row.revision,
                    apply_id=row.apply_id,
                    base_revision=row.base_revision,
                    config_hash=row.config_hash,
                    status=row.status,
                    canonical_spec_json=row.canonical_spec_json,
                    diff_summary_json=row.diff_summary_json,
                    created_at_ms=row.created_at_ms,
                    updated_at_ms=row.updated_at_ms,
                )
        return None


def _persisted_viewer(viewer: DomainViewerInstance) -> PersistedViewerInstance:
    return PersistedViewerInstance(
        session_id=viewer.session_id,
        viewer_instance_id=viewer.viewer_instance_id,
        persona_id=viewer.persona_id,
        persona_revision=viewer.persona_revision,
        ordinal=viewer.ordinal,
        display_name=viewer.display_name,
        micro_variant_json=canonical_json(viewer.variant.model_dump(mode="json")),
        created_epoch=viewer.audience_epoch,
        state=(
            "removed"
            if viewer.lifecycle_state is ViewerLifecycleState.REMOVED
            else "active"
        ),
        username=viewer.username,
        avatar_seed=viewer.avatar_seed,
        color_seed=viewer.color_seed,
        locale=viewer.locale,
        persona_content_hash=viewer.persona_content_hash,
        presence_state=viewer.lifecycle_state.value,
        presence_revision=viewer.presence_revision,
        moderation_revision=viewer.moderation_revision,
        behavior_revision=viewer.behavior_revision,
        joined_at_ms=viewer.joined_at_ms,
        last_left_at_ms=viewer.last_left_at_ms,
        join_count=viewer.join_count,
        muted_until_ms=viewer.muted_until_ms,
        mute_reason=viewer.mute_reason,
        kicked_at_ms=viewer.kicked_at_ms,
        kick_reason=viewer.kick_reason,
        viewer_sequence=viewer.viewer_sequence,
        behavior_state_json=canonical_json(viewer.private_state.model_dump(mode="json")),
        created_at_ms=viewer.created_at_ms,
        updated_at_ms=viewer.created_at_ms,
    )


def _response(
    spec: CanonicalRuntimeSpec,
    pool: ViewerPoolSnapshot,
    *,
    recovered: bool,
    apply_id: str | None = None,
    diff: RuntimeDiffSummary | None = None,
) -> RuntimeSessionSnapshot:
    return RuntimeSessionSnapshot(
        session_id=pool.session_id,
        room_id=pool.room_id,
        audience_epoch=pool.audience_epoch,
        config_revision=spec.config_revision,
        config_hash=spec.config_hash(),
        canonical_runtime_spec=spec,
        viewers=pool.viewers,
        apply_id=apply_id,
        diff=RuntimeDiffSummary() if diff is None else diff,
        recovered=recovered,
    )


def _reconciliation_diff(
    reconciliation: ViewerPoolReconciliation,
) -> RuntimeDiffSummary:
    return RuntimeDiffSummary(
        added_viewer_ids=list(reconciliation.added_viewer_ids),
        retained_viewer_ids=list(reconciliation.retained_viewer_ids),
        reset_viewer_ids=list(reconciliation.reset_viewer_ids),
        removed_viewer_ids=list(reconciliation.removed_viewer_ids),
    )


def _is_recovered(recovery_json: str | None) -> bool:
    if not recovery_json:
        return False
    try:
        recovery = json.loads(recovery_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return recovery.get("recovered") is True
