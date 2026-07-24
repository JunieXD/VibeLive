from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TypeVar

from advx_backend.application.runtime_provider import RuntimeProviderGeneration
from advx_backend.application.viewer_pool_service import ViewerPoolSnapshot
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CommittedRuntime:
    session_id: str
    spec: CanonicalRuntimeSpec
    audience_epoch: int
    pool: ViewerPoolSnapshot
    provider_generation: RuntimeProviderGeneration | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    accepting_results: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeStateDebugSnapshot:
    session_id: str
    spec: CanonicalRuntimeSpec
    audience_epoch: int
    pool: ViewerPoolSnapshot
    accepting_results: bool


class RuntimeStateStore:
    """Single-process observation boundary for config swaps and result fences."""

    def __init__(self) -> None:
        self._states: dict[str, CommittedRuntime] = {}
        self._claimed_sequences: dict[str, dict[str, int]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._effect_locks: dict[str, asyncio.Lock] = {}
        self._effect_owners: dict[str, asyncio.Task[object]] = {}
        self._effect_depths: dict[str, int] = {}
        self._guard = asyncio.Lock()

    async def activate(self, state: CommittedRuntime) -> None:
        async with self.effect_boundary(state.session_id):
            async with self.boundary(state.session_id):
                self._activate_locked(state)

    async def replace(self, state: CommittedRuntime) -> None:
        async with self.effect_boundary(state.session_id):
            async with self.boundary(state.session_id):
                self._replace_locked(state)

    async def activate_after(
        self,
        state: CommittedRuntime,
        persist: Callable[[], Awaitable[None]],
    ) -> None:
        """Publish a first runtime state only after its durable commit succeeds."""

        async with self.effect_boundary(state.session_id):
            async with self.boundary(state.session_id):
                existing = self._states.get(state.session_id)
                self._validate_activation_locked(state)
                await persist()
                self._states[state.session_id] = state
                if existing is None:
                    self._initialize_claims_locked(state)

    async def replace_after(
        self,
        state: CommittedRuntime,
        persist: Callable[[], Awaitable[None]],
    ) -> None:
        """Hold the wave boundary across persistence and the in-memory swap."""

        async with self.effect_boundary(state.session_id):
            async with self.boundary(state.session_id):
                self._validate_replacement_locked(state)
                await persist()
                self._replace_state_locked(state)

    async def stop(self, session_id: str) -> None:
        async with self.effect_boundary(session_id):
            async with self.boundary(session_id):
                current = self._states.get(session_id)
                if current is not None:
                    self._states[session_id] = replace(
                        current,
                        accepting_results=False,
                    )
                self._claimed_sequences.pop(session_id, None)

    async def start_session(self, session_id: str) -> None:
        del session_id

    async def stop_session(self, session_id: str) -> None:
        await self.stop(session_id)

    async def snapshot(self, session_id: str) -> CommittedRuntime:
        async with self.boundary(session_id):
            state = self._states.get(session_id)
            if state is None:
                raise KeyError(session_id)
            return state

    async def debug_snapshot(self, session_id: str) -> RuntimeStateDebugSnapshot:
        """Expose only the immutable runtime values safe for redacted diagnostics."""

        state = await self.snapshot(session_id)
        return RuntimeStateDebugSnapshot(
            session_id=state.session_id,
            spec=state.spec,
            audience_epoch=state.audience_epoch,
            pool=state.pool,
            accepting_results=state.accepting_results,
        )

    async def accepts(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        room_id: str | None = None,
        namespace_id: str | None = None,
        viewer_instance_id: str | None = None,
        viewer_sequence: int | None = None,
        **_: object,
    ) -> bool:
        async with self.boundary(session_id):
            return self._accepts_locked(
                room_id=room_id,
                namespace_id=namespace_id,
                session_id=session_id,
                audience_epoch=audience_epoch,
                viewer_instance_id=viewer_instance_id,
                viewer_sequence=viewer_sequence,
            )

    async def execute_if_accepting(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        operation: Callable[[], Awaitable[T]],
        room_id: str | None = None,
        namespace_id: str | None = None,
        viewer_instance_id: str | None = None,
        viewer_sequence: int | None = None,
    ) -> tuple[bool, T | None]:
        """Keep the final epoch/sequence fence held through a durable side effect."""

        async with self.effect_boundary(session_id):
            async with self.boundary(session_id):
                if not self._accepts_locked(
                    room_id=room_id,
                    namespace_id=namespace_id,
                    session_id=session_id,
                    audience_epoch=audience_epoch,
                    viewer_instance_id=viewer_instance_id,
                    viewer_sequence=viewer_sequence,
                ):
                    return False, None
            return True, await operation()

    async def claim_viewer_sequence(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        viewer_instance_id: str,
        viewer_sequence: int,
    ) -> bool:
        if viewer_sequence < 1:
            return False
        async with self.effect_boundary(session_id):
            async with self.boundary(session_id):
                state = self._states.get(session_id)
                if (
                    state is None
                    or not state.accepting_results
                    or state.spec.room.room_id != room_id
                    or state.audience_epoch != audience_epoch
                ):
                    return False
                viewer = next(
                    (
                        item
                        for item in state.pool.viewers
                        if item.viewer_instance_id == viewer_instance_id
                    ),
                    None,
                )
                if viewer is None or not self._viewer_is_active(viewer):
                    return False
                claims = self._claimed_sequences.setdefault(session_id, {})
                current = claims.get(viewer_instance_id, viewer.viewer_sequence)
                if viewer_sequence != current + 1:
                    return False
                claims[viewer_instance_id] = viewer_sequence
                return True

    @asynccontextmanager
    async def boundary(self, session_id: str) -> AsyncIterator[None]:
        lock = await self._lock_for(session_id)
        async with lock:
            yield

    @asynccontextmanager
    async def effect_boundary(self, session_id: str) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("effect boundary requires an asyncio task")
        if self._effect_owners.get(session_id) is task:
            self._effect_depths[session_id] += 1
            try:
                yield
            finally:
                self._effect_depths[session_id] -= 1
            return
        lock = await self._effect_lock_for(session_id)
        async with lock:
            self._effect_owners[session_id] = task
            self._effect_depths[session_id] = 1
            try:
                yield
            finally:
                self._effect_depths.pop(session_id, None)
                self._effect_owners.pop(session_id, None)

    @asynccontextmanager
    async def provider_lease(
        self,
        *,
        session_id: str,
        audience_epoch: int,
    ) -> AsyncIterator[RuntimeProviderGeneration]:
        async with self.boundary(session_id):
            state = self._states.get(session_id)
            if (
                state is None
                or not state.accepting_results
                or state.audience_epoch != audience_epoch
                or state.provider_generation is None
            ):
                raise RuntimeError("provider generation does not match the active runtime")
            lease = state.provider_generation.lease()
            generation = await lease.__aenter__()
        try:
            yield generation
        finally:
            await lease.__aexit__(None, None, None)

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(session_id, asyncio.Lock())

    async def _effect_lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._guard:
            return self._effect_locks.setdefault(session_id, asyncio.Lock())

    def _accepts_locked(
        self,
        *,
        session_id: str,
        audience_epoch: int,
        room_id: str | None,
        namespace_id: str | None,
        viewer_instance_id: str | None,
        viewer_sequence: int | None,
    ) -> bool:
        state = self._states.get(session_id)
        if (
            state is None
            or not state.accepting_results
            or (room_id is not None and state.spec.room.room_id != room_id)
            or (
                namespace_id is not None
                and self._active_namespace(state.spec) != namespace_id
            )
            or state.audience_epoch != audience_epoch
        ):
            return False
        if viewer_instance_id is None:
            return True
        viewer = next(
            (
                item
                for item in state.pool.viewers
                if item.viewer_instance_id == viewer_instance_id
            ),
            None,
        )
        if viewer is None or not self._viewer_is_active(viewer):
            return False
        if viewer_sequence is None:
            return False
        claimed = self._claimed_sequences.get(session_id, {}).get(
            viewer_instance_id
        )
        return claimed == viewer_sequence

    @staticmethod
    def _active_namespace(spec: CanonicalRuntimeSpec) -> str:
        return next(
            getattr(mode, "namespace_id", mode.mode_id)
            for mode in spec.modes
            if mode.mode_id == spec.active_mode_id
        )

    @staticmethod
    def _viewer_is_active(viewer: object) -> bool:
        lifecycle = getattr(viewer, "lifecycle_state", "active")
        return getattr(lifecycle, "value", lifecycle) == "active"

    def _activate_locked(self, state: CommittedRuntime) -> None:
        existing = self._states.get(state.session_id)
        self._validate_activation_locked(state)
        self._states[state.session_id] = state
        if existing is None:
            self._initialize_claims_locked(state)

    def _replace_locked(self, state: CommittedRuntime) -> None:
        self._validate_replacement_locked(state)
        self._replace_state_locked(state)

    def _initialize_claims_locked(self, state: CommittedRuntime) -> None:
        self._claimed_sequences[state.session_id] = {
            viewer.viewer_instance_id: viewer.viewer_sequence
            for viewer in state.pool.viewers
        }

    def _replace_state_locked(self, state: CommittedRuntime) -> None:
        current = self._states[state.session_id]
        previous_claims = self._claimed_sequences.get(state.session_id, {})
        retained = self._retained_viewer_ids(current, state)
        claims = {
            viewer.viewer_instance_id: (
                previous_claims.get(viewer.viewer_instance_id, viewer.viewer_sequence)
                if viewer.viewer_instance_id in retained
                else 0
            )
            for viewer in state.pool.viewers
        }
        viewers = [
            viewer.model_copy(
                update={"viewer_sequence": claims[viewer.viewer_instance_id]}
            )
            for viewer in state.pool.viewers
        ]
        normalized = replace(
            state,
            pool=state.pool.model_copy(update={"viewers": viewers}),
        )
        self._states[state.session_id] = normalized
        self._claimed_sequences[state.session_id] = claims

    @staticmethod
    def _retained_viewer_ids(
        current: CommittedRuntime,
        replacement: CommittedRuntime,
    ) -> set[str]:
        if current.spec.active_mode_id != replacement.spec.active_mode_id:
            return set()
        current_personas = {
            persona.persona_id: persona for persona in current.spec.personas
        }
        replacement_personas = {
            persona.persona_id: persona for persona in replacement.spec.personas
        }
        current_mode = next(
            mode
            for mode in current.spec.modes
            if mode.mode_id == current.spec.active_mode_id
        )
        replacement_mode = next(
            mode
            for mode in replacement.spec.modes
            if mode.mode_id == replacement.spec.active_mode_id
        )
        current_by_id = {
            viewer.viewer_instance_id: viewer for viewer in current.pool.viewers
        }
        retained: set[str] = set()
        for viewer in replacement.pool.viewers:
            previous = current_by_id.get(viewer.viewer_instance_id)
            persona_id = viewer.persona_id
            if (
                previous is not None
                and previous.persona_id == persona_id
                and previous.ordinal == viewer.ordinal
                and current_personas.get(persona_id)
                == replacement_personas.get(persona_id)
                and current_mode.persona_overrides.get(persona_id)
                == replacement_mode.persona_overrides.get(persona_id)
            ):
                retained.add(viewer.viewer_instance_id)
        return retained

    def _validate_activation_locked(self, state: CommittedRuntime) -> None:
        current = self._states.get(state.session_id)
        if current is None:
            return
        if current == state:
            return
        raise ValueError("runtime state is already active")

    def _validate_replacement_locked(self, state: CommittedRuntime) -> None:
        current = self._states.get(state.session_id)
        if current is None:
            raise KeyError(state.session_id)
        if state.audience_epoch <= current.audience_epoch:
            raise ValueError("replacement epoch must advance")
