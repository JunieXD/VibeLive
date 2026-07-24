from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from advx_backend.application.ports.session import IdGenerator
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec
from advx_backend.domain.persona import ModeDefinition, PersonaOverride, PersonaTemplate
from advx_backend.domain.viewer import (
    ViewerInstance,
    ViewerInstanceVariant,
    ViewerPrivateState,
)


class ViewerPoolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ViewerPoolSnapshot(ViewerPoolModel):
    room_id: str
    session_id: str
    audience_epoch: int = Field(ge=1)
    mode_id: str
    session_seed: str
    viewers: list[ViewerInstance] = Field(max_length=32)


class ViewerPoolReconciliation(ViewerPoolModel):
    snapshot: ViewerPoolSnapshot
    retained_viewer_ids: tuple[str, ...] = ()
    reset_viewer_ids: tuple[str, ...] = ()
    added_viewer_ids: tuple[str, ...] = ()
    removed_viewer_ids: tuple[str, ...] = ()


class ViewerPoolService:
    """Compile a deterministic, weighted Viewer pool for one logical Session."""

    def __init__(self, *, id_generator: IdGenerator) -> None:
        self._id_generator = id_generator
        self._cache: dict[tuple[str, str, int, str, str], ViewerPoolSnapshot] = {}
        self._spec_by_snapshot: dict[int, CanonicalRuntimeSpec] = {}
        self._spec_by_scope: dict[tuple[str, str, int, str], CanonicalRuntimeSpec] = {}

    def create_pool(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        session_seed: str,
        spec: CanonicalRuntimeSpec,
    ) -> ViewerPoolSnapshot:
        self._validate_scope(
            room_id=room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            session_seed=session_seed,
            spec=spec,
        )
        cache_key = (
            room_id,
            session_id,
            audience_epoch,
            session_seed,
            spec.config_hash(),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        mode = self._active_mode(spec)
        personas = {persona.persona_id: persona for persona in spec.personas}
        allocations = self._allocate(mode, personas)
        viewers: list[ViewerInstance] = []
        for persona_id in mode.persona_ids:
            persona = personas.get(persona_id)
            if persona is None:
                continue
            count = allocations.get(persona_id, 0)
            for ordinal in range(1, count + 1):
                viewers.append(
                    self._new_viewer(
                        room_id=room_id,
                        session_id=session_id,
                        audience_epoch=audience_epoch,
                        session_seed=session_seed,
                        persona=persona,
                        override=mode.persona_overrides.get(persona_id),
                        ordinal=ordinal,
                        duplicate_count=count,
                        created_at_ms=spec.room.updated_at_ms,
                    )
                )

        snapshot = ViewerPoolSnapshot(
            room_id=room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            mode_id=mode.mode_id,
            session_seed=session_seed,
            viewers=viewers,
        )
        self._cache[cache_key] = snapshot
        self._spec_by_snapshot[id(snapshot)] = spec
        self._spec_by_scope[
            (room_id, session_id, audience_epoch, mode.mode_id)
        ] = spec
        return snapshot

    def reconcile(
        self,
        *,
        current: ViewerPoolSnapshot,
        next_epoch: int,
        spec: CanonicalRuntimeSpec,
    ) -> ViewerPoolReconciliation:
        if next_epoch <= current.audience_epoch:
            raise ValueError("next_epoch must advance audience_epoch")
        if spec.room.room_id != current.room_id:
            raise ValueError("runtime spec belongs to a different Room")

        target = self.create_pool(
            room_id=current.room_id,
            session_id=current.session_id,
            audience_epoch=next_epoch,
            session_seed=current.session_seed,
            spec=spec,
        )
        previous_spec = self._spec_by_snapshot.get(id(current)) or self._spec_by_scope.get(
            (
                current.room_id,
                current.session_id,
                current.audience_epoch,
                current.mode_id,
            )
        )
        if current.mode_id != target.mode_id:
            return ViewerPoolReconciliation(
                snapshot=target,
                added_viewer_ids=tuple(viewer.viewer_instance_id for viewer in target.viewers),
                removed_viewer_ids=tuple(
                    viewer.viewer_instance_id for viewer in current.viewers
                ),
            )

        current_by_slot = {
            (viewer.persona_id, viewer.ordinal): viewer for viewer in current.viewers
        }
        target_by_slot = {
            (viewer.persona_id, viewer.ordinal): viewer for viewer in target.viewers
        }
        previous_signatures = self._persona_signatures(previous_spec)
        next_signatures = self._persona_signatures(spec)

        reconciled: list[ViewerInstance] = []
        retained: list[str] = []
        reset: list[str] = []
        added: list[str] = []
        for target_viewer in target.viewers:
            slot = (target_viewer.persona_id, target_viewer.ordinal)
            previous = current_by_slot.get(slot)
            if previous is None:
                reconciled.append(target_viewer)
                added.append(target_viewer.viewer_instance_id)
                continue

            unchanged = (
                previous.persona_revision == target_viewer.persona_revision
                and previous_signatures.get(previous.persona_id)
                == next_signatures.get(previous.persona_id)
            )
            private_state = (
                previous.private_state if unchanged else ViewerPrivateState()
            )
            viewer = target_viewer.model_copy(
                update={
                    "viewer_instance_id": previous.viewer_instance_id,
                    "private_state": private_state,
                    "viewer_sequence": previous.viewer_sequence,
                }
            )
            reconciled.append(viewer)
            if unchanged:
                retained.append(viewer.viewer_instance_id)
            else:
                reset.append(viewer.viewer_instance_id)

        removed = tuple(
            viewer.viewer_instance_id
            for slot, viewer in current_by_slot.items()
            if slot not in target_by_slot
        )
        snapshot = target.model_copy(update={"viewers": reconciled})
        self._spec_by_snapshot[id(snapshot)] = spec
        self._spec_by_scope[
            (snapshot.room_id, snapshot.session_id, snapshot.audience_epoch, snapshot.mode_id)
        ] = spec
        return ViewerPoolReconciliation(
            snapshot=snapshot,
            retained_viewer_ids=tuple(retained),
            reset_viewer_ids=tuple(reset),
            added_viewer_ids=tuple(added),
            removed_viewer_ids=removed,
        )

    @staticmethod
    def _active_mode(spec: CanonicalRuntimeSpec) -> ModeDefinition:
        return next(mode for mode in spec.modes if mode.mode_id == spec.active_mode_id)

    @staticmethod
    def _allocate(
        mode: ModeDefinition,
        personas: Mapping[str, PersonaTemplate],
    ) -> dict[str, int]:
        eligible = [
            persona_id
            for persona_id in mode.persona_ids
            if (persona := personas.get(persona_id)) is not None
            and persona.enabled
            and mode.persona_weights.get(persona_id, 0) > 0
        ]
        if not eligible:
            raise ValueError("Mode does not contain an enabled positive-weight Persona")

        total_weight = sum(mode.persona_weights[persona_id] for persona_id in eligible)
        quotas = {
            persona_id: mode.viewer_count
            * mode.persona_weights[persona_id]
            / total_weight
            for persona_id in eligible
        }
        allocations = {
            persona_id: math.floor(quota) for persona_id, quota in quotas.items()
        }
        remaining = mode.viewer_count - sum(allocations.values())
        order = {persona_id: index for index, persona_id in enumerate(mode.persona_ids)}
        ranked = sorted(
            eligible,
            key=lambda persona_id: (
                -(quotas[persona_id] - allocations[persona_id]),
                order[persona_id],
                persona_id,
            ),
        )
        for persona_id in ranked[:remaining]:
            allocations[persona_id] += 1
        return allocations

    def _new_viewer(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        session_seed: str,
        persona: PersonaTemplate,
        override: PersonaOverride | None,
        ordinal: int,
        duplicate_count: int,
        created_at_ms: int,
    ) -> ViewerInstance:
        seed = (
            f"{session_seed}\x00{session_id}\x00{persona.persona_id}\x00"
            f"{ordinal}\x00{audience_epoch}"
        )
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        display_base = (
            override.display_name
            if override is not None and override.display_name is not None
            else persona.display_name
        )
        display_name = (
            f"{display_base}·{ordinal:02d}" if duplicate_count > 1 else display_base
        )
        return ViewerInstance(
            viewer_instance_id=f"viewer-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}",
            room_id=room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            persona_id=persona.persona_id,
            persona_revision=persona.revision,
            ordinal=ordinal,
            display_name=display_name,
            variant=ViewerInstanceVariant(
                expression_length=self._unit(digest, 0),
                skepticism=self._unit(digest, 2),
                encouragement=self._unit(digest, 4),
                meme_affinity=self._unit(digest, 6),
                focus=(persona.traits[0] if persona.traits else persona.role),
                silence_tendency=self._unit(digest, 8),
            ),
            created_at_ms=created_at_ms,
        )

    @staticmethod
    def _unit(digest: bytes, offset: int) -> float:
        return int.from_bytes(digest[offset : offset + 2], "big") / 65_535

    @staticmethod
    def _persona_signatures(
        spec: CanonicalRuntimeSpec | None,
    ) -> dict[str, tuple[object, ...]]:
        if spec is None:
            return {}
        mode = ViewerPoolService._active_mode(spec)
        return {
            persona.persona_id: (
                persona.revision,
                persona.content_hash,
                mode.persona_overrides.get(persona.persona_id),
            )
            for persona in spec.personas
        }

    @staticmethod
    def _validate_scope(
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        session_seed: str,
        spec: CanonicalRuntimeSpec,
    ) -> None:
        if not room_id or not session_id or not session_seed:
            raise ValueError("room_id, session_id and session_seed must not be empty")
        if audience_epoch < 1:
            raise ValueError("audience_epoch must be at least one")
        if spec.room.room_id != room_id:
            raise ValueError("runtime spec belongs to a different Room")
