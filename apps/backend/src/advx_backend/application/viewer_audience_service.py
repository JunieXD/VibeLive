from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from advx_backend.application.ports.session import Clock
from advx_backend.application.realtime_broker import RealtimeBroker
from advx_backend.application.runtime_state import RuntimeStateStore
from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.contracts.audience import (
    SessionAudienceSnapshot,
    ViewerPresenceEvent,
    ViewerSnapshot,
)
from advx_backend.contracts.viewer_runtime import (
    ViewerBarrageEvent,
    ViewerGenerationRequest,
)
from advx_backend.domain.viewer import (
    ViewerInstance,
    ViewerLifecycleState,
    ViewerPrivateState,
)
from advx_backend.infrastructure.persistence.sqlite.models import (
    SessionRecordRow,
    SessionViewerInstanceRow,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    SQLiteViewerInstanceRepository,
    canonical_json,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    ViewerInstance as PersistedViewerInstance,
)


class ViewerAudienceError(RuntimeError):
    code = "viewer_audience_error"


class ViewerNotFoundError(ViewerAudienceError):
    code = "viewer_not_found"


class ViewerStateConflictError(ViewerAudienceError):
    code = "viewer_state_conflict"


@dataclass(frozen=True, slots=True)
class _ViewerCommandRecord:
    fingerprint: tuple[object, ...]
    viewer: ViewerInstance


class ViewerAudienceService:
    """Authoritative session-scoped Viewer snapshot and moderation operations."""

    def __init__(
        self,
        *,
        runtime_state: RuntimeStateStore,
        session_factory: async_sessionmaker[AsyncSession],
        broker: RealtimeBroker,
        clock: Clock,
        viewer_pool: ViewerPoolService,
    ) -> None:
        self._runtime_state = runtime_state
        self._session_factory = session_factory
        self._broker = broker
        self._clock = clock
        self._viewer_pool = viewer_pool
        self._cancel_viewer: Callable[..., Awaitable[None]] | None = None
        self._commands: OrderedDict[
            tuple[str, str],
            _ViewerCommandRecord,
        ] = OrderedDict()

    def bind_cancel_viewer(self, cancel: Callable[..., Awaitable[None]]) -> None:
        self._cancel_viewer = cancel

    async def start_session(self, session_id: str) -> None:
        del session_id

    async def stop_session(self, session_id: str) -> None:
        try:
            state = await self._runtime_state.snapshot(session_id)
        except KeyError:
            return
        now_ms = self._clock.now_ms()
        for current in tuple(state.pool.viewers):
            if current.lifecycle_state in {
                ViewerLifecycleState.ENDED,
                ViewerLifecycleState.REMOVED,
            }:
                continue

            def transform(value: object) -> ViewerInstance:
                viewer = self._viewer(value)
                return viewer.model_copy(
                    update={
                        "lifecycle_state": ViewerLifecycleState.ENDED,
                        "private_state": ViewerPrivateState(),
                        "presence_revision": viewer.presence_revision + 1,
                        "behavior_revision": viewer.behavior_revision + 1,
                        "muted_until_ms": None,
                        "mute_reason": None,
                        "removed_at_ms": now_ms,
                    }
                )

            await self._runtime_state.update_viewer(
                session_id=session_id,
                viewer_instance_id=current.viewer_instance_id,
                update=transform,
                persist=self._persist,
            )

    async def current(self, session_id: str) -> SessionAudienceSnapshot:
        await self.reconcile_population(session_id)
        state = await self._runtime_state.snapshot(session_id)
        mode = next(
            item for item in state.spec.modes if item.mode_id == state.spec.active_mode_id
        )
        personas = {
            persona.persona_id: persona.display_name for persona in state.spec.personas
        }
        viewers = (
            [
                ViewerSnapshot.from_domain(
                    viewer,
                    persona_display_name=personas.get(
                        viewer.persona_id,
                        viewer.persona_id,
                    ),
                )
                for viewer in state.pool.viewers
                if viewer.lifecycle_state is not ViewerLifecycleState.REMOVED
            ]
            if state.accepting_results
            else []
        )
        return SessionAudienceSnapshot(
            session_id=state.session_id,
            room_id=state.spec.room.room_id,
            audience_epoch=state.audience_epoch,
            population_revision=state.population_revision,
            target_concurrent_viewers=mode.target_concurrent_viewers,
            active_count=sum(
                viewer.presence_state is ViewerLifecycleState.ACTIVE
                for viewer in viewers
            ),
            viewers=viewers,
        )

    async def mute(
        self,
        session_id: str,
        viewer_instance_id: str,
        *,
        command_id: str,
        duration_ms: int,
        reason: str | None,
    ) -> ViewerSnapshot:
        now_ms = self._clock.now_ms()
        key = (session_id, command_id)
        fingerprint = ("mute", viewer_instance_id, duration_ms, reason)
        cached = self._cached_command(key, fingerprint)
        if cached is not None:
            return await self._public_viewer(session_id, cached)

        def transform(value: object) -> ViewerInstance:
            viewer = self._viewer(value)
            self._require_active(viewer)
            return viewer.model_copy(
                update={
                    "muted_until_ms": max(
                        viewer.muted_until_ms or 0,
                        now_ms + duration_ms,
                    ),
                    "mute_reason": reason,
                    "moderation_revision": viewer.moderation_revision + 1,
                    "behavior_revision": viewer.behavior_revision + 1,
                }
            )

        viewer = await self._update(session_id, viewer_instance_id, transform)
        self._remember(key, fingerprint, viewer)
        await self._cancel(viewer_instance_id, "viewer_muted")
        await self._publish("viewer.muted", viewer)
        return await self._public_viewer(session_id, viewer)

    async def unmute(
        self,
        session_id: str,
        viewer_instance_id: str,
        *,
        command_id: str,
    ) -> ViewerSnapshot:
        key = (session_id, command_id)
        fingerprint = ("unmute", viewer_instance_id)
        cached = self._cached_command(key, fingerprint)
        if cached is not None:
            return await self._public_viewer(session_id, cached)

        changed = False

        def transform(value: object) -> ViewerInstance:
            nonlocal changed
            viewer = self._viewer(value)
            self._require_active(viewer)
            if viewer.muted_until_ms is None:
                return viewer
            changed = True
            return viewer.model_copy(
                update={
                    "muted_until_ms": None,
                    "mute_reason": None,
                    "moderation_revision": viewer.moderation_revision + 1,
                    "behavior_revision": viewer.behavior_revision + 1,
                }
            )

        viewer = await self._update(session_id, viewer_instance_id, transform)
        self._remember(key, fingerprint, viewer)
        if changed:
            await self._publish("viewer.unmuted", viewer)
        return await self._public_viewer(session_id, viewer)

    async def kick(
        self,
        session_id: str,
        viewer_instance_id: str,
        *,
        command_id: str,
        reason: str | None,
    ) -> ViewerSnapshot:
        now_ms = self._clock.now_ms()
        key = (session_id, command_id)
        fingerprint = ("kick", viewer_instance_id, reason)
        cached = self._cached_command(key, fingerprint)
        if cached is not None:
            return await self._public_viewer(session_id, cached)

        def transform(value: object) -> ViewerInstance:
            viewer = self._viewer(value)
            self._require_active(viewer)
            return viewer.model_copy(
                update={
                    "lifecycle_state": ViewerLifecycleState.KICKED,
                    "presence_revision": viewer.presence_revision + 1,
                    "moderation_revision": viewer.moderation_revision + 1,
                    "behavior_revision": viewer.behavior_revision + 1,
                    "muted_until_ms": None,
                    "mute_reason": None,
                    "kicked_at_ms": now_ms,
                    "kick_reason": reason,
                    "removed_at_ms": now_ms,
                }
            )

        viewer = await self._update(session_id, viewer_instance_id, transform)
        self._remember(key, fingerprint, viewer)
        await self._cancel(viewer_instance_id, "viewer_kicked")
        await self._publish("viewer.kicked", viewer)
        await self.reconcile_population(session_id)
        return await self._public_viewer(session_id, viewer)

    async def reconcile_population(
        self,
        session_id: str,
        *,
        observation_id: str | None = None,
    ) -> None:
        await self._expire_mutes(session_id)
        state = await self._runtime_state.snapshot(session_id)
        if not state.accepting_results:
            return
        mode = next(
            item for item in state.spec.modes if item.mode_id == state.spec.active_mode_id
        )
        active = [viewer for viewer in state.pool.viewers if viewer.is_active()]
        desired_counts = {
            persona_id: count
            for persona_id, count in mode.persona_counts.items()
            if count > 0
        }
        active_counts = {
            persona_id: sum(viewer.persona_id == persona_id for viewer in active)
            for persona_id in desired_counts
        }
        deficit = mode.viewer_count - len(active)
        if deficit > 0:
            rejoinable = sorted(
                (
                    viewer
                    for viewer in state.pool.viewers
                    if viewer.lifecycle_state is ViewerLifecycleState.LEFT
                    and active_counts.get(viewer.persona_id, 0)
                    < desired_counts.get(viewer.persona_id, 0)
                ),
                key=lambda viewer: (viewer.last_left_at_ms or 0, viewer.ordinal),
            )
            rejoined = 0
            for previous in rejoinable:
                if rejoined >= deficit:
                    break
                if active_counts.get(previous.persona_id, 0) >= desired_counts.get(
                    previous.persona_id,
                    0,
                ):
                    continue
                viewer = await self._rejoin(previous)
                await self._publish("viewer.rejoined", viewer)
                active_counts[viewer.persona_id] = active_counts.get(viewer.persona_id, 0) + 1
                rejoined += 1
            state = await self._runtime_state.snapshot(session_id)
            active_count = sum(viewer.is_active() for viewer in state.pool.viewers)
            while active_count < mode.viewer_count:
                viewer = self._viewer_pool.create_replacement(
                    current=state.pool,
                    spec=state.spec,
                    created_at_ms=self._clock.now_ms(),
                )
                await self._runtime_state.add_viewer(
                    session_id=session_id,
                    viewer=viewer,
                    persist=self._persist_new,
                )
                await self._publish("viewer.joined", viewer)
                state = await self._runtime_state.snapshot(session_id)
                active_count += 1

        if observation_id is not None:
            await self._maybe_leave(session_id, observation_id)

    async def record_published(
        self,
        request: ViewerGenerationRequest,
        event: object,
    ) -> None:
        if isinstance(event, ViewerBarrageEvent):
            events = (event,)
        elif isinstance(event, tuple) and event and all(
            isinstance(item, ViewerBarrageEvent) for item in event
        ):
            events = event
        else:
            return
        latest_event = events[-1]

        def transform(value: object) -> ViewerInstance:
            viewer = self._viewer(value)
            if not viewer.is_active():
                return viewer
            state = viewer.private_state
            published = [
                *state.published_event_ids,
                *(item.barrage_id for item in events),
            ][-64:]
            target_viewer_id = (
                None
                if latest_event.target is None
                else latest_event.target.viewer_instance_id
            )
            affinities = dict(state.peer_affinities)
            if target_viewer_id is not None:
                affinities[target_viewer_id] = min(
                    1.0,
                    affinities.get(target_viewer_id, 0.0) + 0.05,
                )
            private_state = state.model_copy(
                update={
                    "revision": state.revision + 1,
                    "published_event_ids": published,
                    "cooldown_until_ms": latest_event.created_at_ms
                    + max(15_000, request.persona.cooldown_ms),
                    "last_spoke_at_ms": latest_event.created_at_ms,
                    "last_reacted_at_ms": latest_event.created_at_ms,
                    "fatigue": min(1.0, state.fatigue + 0.08),
                    "engagement": min(1.0, state.engagement + 0.04),
                    "current_target_viewer_id": target_viewer_id,
                    "peer_affinities": affinities,
                    "silence_streak": 0,
                    "speech_streak": state.speech_streak + 1,
                }
            )
            return viewer.model_copy(
                update={
                    "private_state": private_state,
                    "viewer_sequence": max(
                        viewer.viewer_sequence,
                        request.viewer_sequence,
                    ),
                    "behavior_revision": viewer.behavior_revision + 1,
                }
            )

        await self._runtime_state.update_viewer(
            session_id=request.session_id,
            viewer_instance_id=request.viewer_instance_id,
            update=transform,
            persist=self._persist_behavior,
            increment_population=False,
        )

    async def record_silence(self, request: ViewerGenerationRequest) -> None:
        def transform(value: object) -> ViewerInstance:
            viewer = self._viewer(value)
            if not viewer.is_active():
                return viewer
            state = viewer.private_state
            private_state = state.model_copy(
                update={
                    "revision": state.revision + 1,
                    "fatigue": max(0.0, state.fatigue - 0.02),
                    "silence_streak": state.silence_streak + 1,
                    "speech_streak": 0,
                    "last_reacted_at_ms": self._clock.now_ms(),
                }
            )
            return viewer.model_copy(
                update={
                    "private_state": private_state,
                    "viewer_sequence": max(
                        viewer.viewer_sequence,
                        request.viewer_sequence,
                    ),
                    "behavior_revision": viewer.behavior_revision + 1,
                }
            )

        await self._runtime_state.update_viewer(
            session_id=request.session_id,
            viewer_instance_id=request.viewer_instance_id,
            update=transform,
            persist=self._persist_behavior,
            increment_population=False,
        )

    async def _expire_mutes(self, session_id: str) -> None:
        now_ms = self._clock.now_ms()
        state = await self._runtime_state.snapshot(session_id)
        expired = [
            viewer.viewer_instance_id
            for viewer in state.pool.viewers
            if viewer.is_active()
            and viewer.muted_until_ms is not None
            and viewer.muted_until_ms <= now_ms
        ]
        for viewer_id in expired:
            await self.unmute(
                session_id,
                viewer_id,
                command_id=f"automatic-unmute:{viewer_id}:{now_ms}",
            )

    async def _rejoin(self, previous: ViewerInstance) -> ViewerInstance:
        now_ms = self._clock.now_ms()

        def transform(value: object) -> ViewerInstance:
            viewer = self._viewer(value)
            if viewer.lifecycle_state is not ViewerLifecycleState.LEFT:
                return viewer
            return viewer.model_copy(
                update={
                    "lifecycle_state": ViewerLifecycleState.ACTIVE,
                    "presence_revision": viewer.presence_revision + 1,
                    "behavior_revision": viewer.behavior_revision + 1,
                    "joined_at_ms": now_ms,
                    "join_count": viewer.join_count + 1,
                }
            )

        return self._viewer(
            await self._runtime_state.update_viewer(
                session_id=previous.session_id,
                viewer_instance_id=previous.viewer_instance_id,
                update=transform,
                persist=self._persist,
            )
        )

    async def _maybe_leave(self, session_id: str, observation_id: str) -> None:
        state = await self._runtime_state.snapshot(session_id)
        active = [viewer for viewer in state.pool.viewers if viewer.is_active()]
        if len(active) <= 1:
            return
        ranked = sorted(
            active,
            key=lambda viewer: hashlib.sha256(
                (
                    f"{state.pool.session_seed}\0{observation_id}\0"
                    f"{viewer.viewer_instance_id}\0leave-v1"
                ).encode()
            ).digest(),
        )
        candidate = ranked[0]
        draw = int.from_bytes(
            hashlib.sha256(
                f"{observation_id}\0{candidate.viewer_instance_id}\0leave-draw".encode()
            ).digest()[:8],
            "big",
        ) / ((1 << 64) - 1)
        probability = 0.015 * (1.0 - candidate.variant.stay_duration_tendency)
        if draw >= probability:
            return
        now_ms = self._clock.now_ms()

        def transform(value: object) -> ViewerInstance:
            viewer = self._viewer(value)
            if not viewer.is_active():
                return viewer
            return viewer.model_copy(
                update={
                    "lifecycle_state": ViewerLifecycleState.LEFT,
                    "presence_revision": viewer.presence_revision + 1,
                    "behavior_revision": viewer.behavior_revision + 1,
                    "last_left_at_ms": now_ms,
                    "muted_until_ms": None,
                    "mute_reason": None,
                }
            )

        viewer = self._viewer(
            await self._runtime_state.update_viewer(
                session_id=session_id,
                viewer_instance_id=candidate.viewer_instance_id,
                update=transform,
                persist=self._persist,
            )
        )
        await self._cancel(viewer.viewer_instance_id, "viewer_left")
        await self._publish("viewer.left", viewer)

    async def _update(
        self,
        session_id: str,
        viewer_instance_id: str,
        transform: Callable[[object], ViewerInstance],
    ) -> ViewerInstance:
        try:
            value = await self._runtime_state.update_viewer(
                session_id=session_id,
                viewer_instance_id=viewer_instance_id,
                update=transform,
                persist=self._persist,
            )
        except KeyError as error:
            raise ViewerNotFoundError(str(error.args[0])) from error
        return self._viewer(value)

    async def _persist(self, value: object) -> None:
        viewer = self._viewer(value)
        state = (
            "removed"
            if viewer.lifecycle_state
            in {
                ViewerLifecycleState.KICKED,
                ViewerLifecycleState.ENDED,
                ViewerLifecycleState.REMOVED,
            }
            else "active"
        )
        async with self._session_factory() as session:
            await session.execute(
                update(SessionViewerInstanceRow)
                .where(
                    SessionViewerInstanceRow.session_id == viewer.session_id,
                    SessionViewerInstanceRow.viewer_instance_id
                    == viewer.viewer_instance_id,
                )
                .values(
                    state=state,
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
                    updated_at_ms=self._clock.now_ms(),
                )
            )
            await session.execute(
                update(SessionRecordRow)
                .where(SessionRecordRow.session_id == viewer.session_id)
                .values(
                    population_revision=SessionRecordRow.population_revision + 1
                )
            )
            await session.commit()

    async def _persist_behavior(self, value: object) -> None:
        viewer = self._viewer(value)
        async with self._session_factory() as session:
            await session.execute(
                update(SessionViewerInstanceRow)
                .where(
                    SessionViewerInstanceRow.session_id == viewer.session_id,
                    SessionViewerInstanceRow.viewer_instance_id
                    == viewer.viewer_instance_id,
                )
                .values(
                    behavior_revision=viewer.behavior_revision,
                    viewer_sequence=viewer.viewer_sequence,
                    behavior_state_json=canonical_json(
                        viewer.private_state.model_dump(mode="json")
                    ),
                    updated_at_ms=self._clock.now_ms(),
                )
            )
            await session.commit()

    async def _persist_new(self, value: object) -> None:
        viewer = self._viewer(value)
        persisted = PersistedViewerInstance(
            session_id=viewer.session_id,
            viewer_instance_id=viewer.viewer_instance_id,
            persona_id=viewer.persona_id,
            persona_revision=viewer.persona_revision,
            ordinal=viewer.ordinal,
            display_name=viewer.display_name,
            micro_variant_json=canonical_json(viewer.variant.model_dump(mode="json")),
            created_epoch=viewer.audience_epoch,
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
            join_count=viewer.join_count,
            viewer_sequence=viewer.viewer_sequence,
            behavior_state_json=canonical_json(
                viewer.private_state.model_dump(mode="json")
            ),
            created_at_ms=viewer.created_at_ms,
            updated_at_ms=viewer.created_at_ms,
        )
        async with self._session_factory() as session:
            await SQLiteViewerInstanceRepository(session).add_all([persisted])
            await session.execute(
                update(SessionRecordRow)
                .where(SessionRecordRow.session_id == viewer.session_id)
                .values(
                    next_creation_ordinal=func.max(
                        SessionRecordRow.next_creation_ordinal,
                        viewer.ordinal + 1,
                    ),
                    population_revision=SessionRecordRow.population_revision + 1,
                )
            )
            await session.commit()

    async def _publish(self, event_type: str, viewer: ViewerInstance) -> None:
        state = await self._runtime_state.snapshot(viewer.session_id)
        snapshot = await self._public_viewer(viewer.session_id, viewer)
        await self._broker.publish_viewer_event(
            ViewerPresenceEvent(
                type=event_type,
                session_id=viewer.session_id,
                audience_epoch=state.audience_epoch,
                population_revision=state.population_revision,
                occurred_at_ms=self._clock.now_ms(),
                viewer=snapshot,
            )
        )

    async def _public_viewer(
        self,
        session_id: str,
        viewer: ViewerInstance,
    ) -> ViewerSnapshot:
        state = await self._runtime_state.snapshot(session_id)
        name = next(
            (
                persona.display_name
                for persona in state.spec.personas
                if persona.persona_id == viewer.persona_id
            ),
            viewer.persona_id,
        )
        return ViewerSnapshot.from_domain(viewer, persona_display_name=name)

    async def _cancel(self, viewer_id: str, reason: str) -> None:
        if self._cancel_viewer is not None:
            await self._cancel_viewer(viewer_id, reason=reason)

    def _remember(
        self,
        key: tuple[str, str],
        fingerprint: tuple[object, ...],
        viewer: ViewerInstance,
    ) -> None:
        self._commands[key] = _ViewerCommandRecord(
            fingerprint=fingerprint,
            viewer=viewer,
        )
        self._commands.move_to_end(key)
        while len(self._commands) > 1_024:
            self._commands.popitem(last=False)

    def _cached_command(
        self,
        key: tuple[str, str],
        fingerprint: tuple[object, ...],
    ) -> ViewerInstance | None:
        cached = self._commands.get(key)
        if cached is None:
            return None
        if cached.fingerprint != fingerprint:
            raise ViewerStateConflictError(
                "command_id was already used with different Viewer command content"
            )
        return cached.viewer

    @staticmethod
    def _viewer(value: object) -> ViewerInstance:
        if not isinstance(value, ViewerInstance):
            raise TypeError("runtime Viewer state is invalid")
        return value

    @staticmethod
    def _require_active(viewer: ViewerInstance) -> None:
        if not viewer.is_active():
            raise ViewerStateConflictError(
                f"Viewer {viewer.viewer_instance_id} is not active"
            )
