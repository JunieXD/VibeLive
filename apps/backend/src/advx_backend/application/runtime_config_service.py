from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from advx_backend.application.ports.session import Clock
from advx_backend.application.viewer_pool_service import (
    ViewerPoolReconciliation,
    ViewerPoolService,
    ViewerPoolSnapshot,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    RuntimeApplyRequest,
    RuntimeApplyResponse,
    RuntimeDiffSummary,
    RuntimeQueryResponse,
)


class RuntimeRepository(Protocol):
    active_spec: CanonicalRuntimeSpec
    active_epoch: int

    async def stage(self, session_id: str, request: RuntimeApplyRequest) -> None: ...

    async def commit(
        self,
        spec: CanonicalRuntimeSpec,
        audience_epoch: int,
    ) -> None: ...

    async def rollback(self, apply_id: str) -> None: ...


class RuntimeCapabilityProbe(Protocol):
    async def probe(self, spec: CanonicalRuntimeSpec) -> None: ...


class RuntimeApplyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PendingRuntimeApply:
    session_id: str
    request: RuntimeApplyRequest
    reconciliation: ViewerPoolReconciliation


class RuntimeConfigService:
    """Stage and atomically expose complete runtime snapshots at wave boundaries."""

    def __init__(
        self,
        *,
        repository: RuntimeRepository,
        viewer_pool: ViewerPoolService,
        capability_probe: RuntimeCapabilityProbe,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._viewer_pool = viewer_pool
        self._capability_probe = capability_probe
        self._clock = clock
        self._pending: dict[str, PendingRuntimeApply] = {}
        self._pools: dict[str, ViewerPoolSnapshot] = {}
        self._lock = asyncio.Lock()

    async def prepare_apply(
        self,
        session_id: str,
        request: RuntimeApplyRequest,
    ) -> PendingRuntimeApply:
        if not session_id:
            raise RuntimeApplyError("session_id must not be empty")
        async with self._lock:
            existing = self._pending.get(request.apply_id)
            if existing is not None:
                if (
                    existing.session_id == session_id
                    and existing.request == request
                ):
                    return existing
                raise RuntimeApplyError("apply_id already refers to a different request")

            active_spec = self._repository.active_spec
            if request.base_revision != active_spec.config_revision:
                raise RuntimeApplyError(
                    f"base revision {request.base_revision} is not current"
                )
            if request.canonical_runtime_spec.config_revision <= request.base_revision:
                raise RuntimeApplyError("config revision must advance")

            await self._repository.stage(session_id, request)
            try:
                await self._capability_probe.probe(request.canonical_runtime_spec)
                current_pool = self._pool_for(
                    session_id=session_id,
                    spec=active_spec,
                    audience_epoch=self._repository.active_epoch,
                )
                reconciliation = self._viewer_pool.reconcile(
                    current=current_pool,
                    next_epoch=self._repository.active_epoch + 1,
                    spec=request.canonical_runtime_spec,
                )
            except Exception as error:
                await self._repository.rollback(request.apply_id)
                raise RuntimeApplyError(str(error)) from error

            pending = PendingRuntimeApply(
                session_id=session_id,
                request=request,
                reconciliation=reconciliation,
            )
            self._pending[request.apply_id] = pending
            return pending

    async def commit_at_wave_boundary(
        self,
        session_id: str,
        apply_id: str,
    ) -> RuntimeApplyResponse:
        async with self._lock:
            pending = self._pending.get(apply_id)
            if pending is None or pending.session_id != session_id:
                raise RuntimeApplyError("pending apply was not found")
            next_epoch = self._repository.active_epoch + 1
            spec = pending.request.canonical_runtime_spec
            await self._repository.commit(spec, next_epoch)
            pool = pending.reconciliation.snapshot
            if pool.audience_epoch != next_epoch:
                pool = pool.model_copy(update={"audience_epoch": next_epoch})
            self._pools[session_id] = pool
            self._pending.pop(apply_id, None)

            reconciliation = pending.reconciliation
            return RuntimeApplyResponse(
                apply_id=apply_id,
                room_id=spec.room.room_id,
                session_id=session_id,
                audience_epoch=next_epoch,
                config_revision=spec.config_revision,
                config_hash=spec.config_hash(),
                applied_at_ms=self._clock.now_ms(),
                diff=RuntimeDiffSummary(
                    added_viewer_ids=list(reconciliation.added_viewer_ids),
                    retained_viewer_ids=list(reconciliation.retained_viewer_ids),
                    reset_viewer_ids=list(reconciliation.reset_viewer_ids),
                    removed_viewer_ids=list(reconciliation.removed_viewer_ids),
                ),
            )

    async def current(self, session_id: str) -> RuntimeQueryResponse:
        async with self._lock:
            spec = self._repository.active_spec
            pool = self._pool_for(
                session_id=session_id,
                spec=spec,
                audience_epoch=self._repository.active_epoch,
            )
            return RuntimeQueryResponse(
                room_id=spec.room.room_id,
                session_id=session_id,
                audience_epoch=self._repository.active_epoch,
                config_revision=spec.config_revision,
                config_hash=spec.config_hash(),
                canonical_runtime_spec=spec,
                viewers=pool.viewers,
            )

    async def discard_pending(self, session_id: str, apply_id: str) -> None:
        async with self._lock:
            pending = self._pending.get(apply_id)
            if pending is None:
                return
            if pending.session_id != session_id:
                raise RuntimeApplyError("pending apply belongs to a different Session")
            await self._repository.rollback(apply_id)
            self._pending.pop(apply_id, None)

    def _pool_for(
        self,
        *,
        session_id: str,
        spec: CanonicalRuntimeSpec,
        audience_epoch: int,
    ) -> ViewerPoolSnapshot:
        current = self._pools.get(session_id)
        if (
            current is not None
            and current.audience_epoch == audience_epoch
            and current.room_id == spec.room.room_id
        ):
            return current
        pool = self._viewer_pool.create_pool(
            room_id=spec.room.room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            session_seed=session_id,
            spec=spec,
        )
        self._pools[session_id] = pool
        return pool

