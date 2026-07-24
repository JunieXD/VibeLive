from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from advx_backend.application.ports.session import IdGenerator
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate
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
    next_creation_ordinal: int = Field(default=1, ge=1, le=129)
    viewers: list[ViewerInstance] = Field(max_length=128)


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
        persona_slots = self._persona_slots(
            mode=mode,
            allocations=allocations,
            session_seed=session_seed,
        )
        viewers: list[ViewerInstance] = []
        for creation_ordinal, persona_id in enumerate(persona_slots, start=1):
            persona = personas.get(persona_id)
            if persona is None:
                continue
            viewers.append(
                self._new_viewer(
                    room_id=room_id,
                    session_id=session_id,
                    audience_epoch=audience_epoch,
                    session_seed=session_seed,
                    persona=persona,
                    ordinal=creation_ordinal,
                    created_at_ms=spec.room.updated_at_ms,
                )
            )

        snapshot = ViewerPoolSnapshot(
            room_id=room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            mode_id=mode.mode_id,
            session_seed=session_seed,
            next_creation_ordinal=(
                max((viewer.ordinal for viewer in viewers), default=0) + 1
            ),
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
        previous_signatures = self._persona_signatures(previous_spec)
        next_signatures = self._persona_signatures(spec)
        next_personas = {
            persona.persona_id: persona
            for persona in spec.personas
            if persona.enabled
        }
        target_count = self._active_mode(spec).target_concurrent_viewers
        retained_active_ids = [
            viewer.viewer_instance_id
            for viewer in sorted(current.viewers, key=lambda item: item.ordinal)
            if viewer.is_active()
        ][:target_count]
        retained_active_ids = set(retained_active_ids)

        reconciled: list[ViewerInstance] = []
        retained: list[str] = []
        reset: list[str] = []
        added: list[str] = []
        removed: list[str] = []
        for previous in sorted(current.viewers, key=lambda item: item.ordinal):
            if (
                previous.is_active()
                and previous.viewer_instance_id not in retained_active_ids
            ):
                removed.append(previous.viewer_instance_id)
                continue

            next_persona = next_personas.get(previous.persona_id)
            if next_persona is None:
                assignment = target.viewers[
                    (previous.ordinal - 1) % len(target.viewers)
                ]
                next_persona = next_personas[assignment.persona_id]
                viewer = previous.model_copy(
                    update={
                        "audience_epoch": next_epoch,
                        "persona_id": next_persona.persona_id,
                        "persona_revision": next_persona.revision,
                        "persona_content_hash": next_persona.content_hash,
                        "variant": assignment.variant,
                        "private_state": ViewerPrivateState(),
                        "behavior_revision": previous.behavior_revision + 1,
                    }
                )
                reconciled.append(viewer)
                reset.append(viewer.viewer_instance_id)
                continue
            unchanged = previous_signatures.get(previous.persona_id) == next_signatures.get(
                previous.persona_id
            )
            private_state = (
                previous.private_state if unchanged else ViewerPrivateState()
            )
            viewer = previous.model_copy(
                update={
                    "audience_epoch": next_epoch,
                    "persona_id": previous.persona_id,
                    "persona_revision": next_persona.revision,
                    "persona_content_hash": next_persona.content_hash,
                    "variant": previous.variant.model_copy(
                        update={
                            "focus": (
                                next_persona.traits[0]
                                if next_persona.traits
                                else next_persona.role
                            )
                        }
                    ),
                    "private_state": private_state,
                    "behavior_revision": (
                        previous.behavior_revision
                        if unchanged
                        else previous.behavior_revision + 1
                    ),
                }
            )
            reconciled.append(viewer)
            if unchanged:
                retained.append(viewer.viewer_instance_id)
            else:
                reset.append(viewer.viewer_instance_id)

        active_count = sum(viewer.is_active() for viewer in reconciled)
        next_creation_ordinal = max(
            current.next_creation_ordinal,
            max((viewer.ordinal for viewer in current.viewers), default=0) + 1,
        )
        snapshot = target.model_copy(
            update={
                "viewers": reconciled,
                "next_creation_ordinal": next_creation_ordinal,
            }
        )
        while active_count < target_count:
            viewer = self.create_replacement(
                current=snapshot,
                spec=spec,
                created_at_ms=spec.room.updated_at_ms,
            )
            reconciled.append(viewer)
            added.append(viewer.viewer_instance_id)
            active_count += 1
            snapshot = snapshot.model_copy(
                update={
                    "viewers": list(reconciled),
                    "next_creation_ordinal": viewer.ordinal + 1,
                }
            )
        self._spec_by_snapshot[id(snapshot)] = spec
        self._spec_by_scope[
            (snapshot.room_id, snapshot.session_id, snapshot.audience_epoch, snapshot.mode_id)
        ] = spec
        return ViewerPoolReconciliation(
            snapshot=snapshot,
            retained_viewer_ids=tuple(retained),
            reset_viewer_ids=tuple(reset),
            added_viewer_ids=tuple(added),
            removed_viewer_ids=tuple(removed),
        )

    def create_replacement(
        self,
        *,
        current: ViewerPoolSnapshot,
        spec: CanonicalRuntimeSpec,
        created_at_ms: int,
    ) -> ViewerInstance:
        ordinal = max(
            current.next_creation_ordinal,
            max((viewer.ordinal for viewer in current.viewers), default=0) + 1,
        )
        if ordinal > 128:
            raise ValueError("Session Viewer creation limit reached")
        mode = self._active_mode(spec)
        personas = {persona.persona_id: persona for persona in spec.personas}
        allocations = self._allocate(mode, personas)
        slots = self._persona_slots(
            mode=mode,
            allocations=allocations,
            session_seed=f"{current.session_seed}\0replacement\0{ordinal}",
        )
        persona = personas[slots[(ordinal - 1) % len(slots)]]
        return self._new_viewer(
            room_id=current.room_id,
            session_id=current.session_id,
            audience_epoch=current.audience_epoch,
            session_seed=current.session_seed,
            persona=persona,
            ordinal=ordinal,
            created_at_ms=created_at_ms,
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
            persona_id: mode.target_concurrent_viewers
            * mode.persona_weights[persona_id]
            / total_weight
            for persona_id in eligible
        }
        allocations = {
            persona_id: math.floor(quota) for persona_id, quota in quotas.items()
        }
        remaining = mode.target_concurrent_viewers - sum(allocations.values())
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

    @staticmethod
    def _persona_slots(
        *,
        mode: ModeDefinition,
        allocations: Mapping[str, int],
        session_seed: str,
    ) -> list[str]:
        slots = [
            (persona_id, ordinal)
            for persona_id in mode.persona_ids
            for ordinal in range(1, allocations.get(persona_id, 0) + 1)
        ]
        slots.sort(
            key=lambda item: (
                hashlib.sha256(
                    (
                        f"{session_seed}\0{mode.mode_id}\0{item[0]}\0"
                        f"{item[1]}\0persona-slot-v2"
                    ).encode()
                ).digest(),
                item,
            )
        )
        return [persona_id for persona_id, _ in slots]

    def _new_viewer(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        session_seed: str,
        persona: PersonaTemplate,
        ordinal: int,
        created_at_ms: int,
    ) -> ViewerInstance:
        seed = f"{session_seed}\x00{session_id}\x00{ordinal}\x00viewer-v2"
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        username = self._username(digest, ordinal)
        return ViewerInstance(
            viewer_instance_id=f"viewer-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}",
            room_id=room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            persona_id=persona.persona_id,
            persona_revision=persona.revision,
            persona_content_hash=persona.content_hash,
            ordinal=ordinal,
            username=username,
            display_name=username,
            avatar_seed=hashlib.sha256(digest + b"avatar").hexdigest()[:24],
            color_seed=hashlib.sha256(digest + b"color").hexdigest()[:16],
            variant=ViewerInstanceVariant(
                activity_baseline=self._unit(digest, 10),
                attention_span=self._unit(digest, 12),
                social_initiative=self._unit(digest, 14),
                reply_affinity=self._unit(digest, 16),
                expression_length=self._unit(digest, 0),
                skepticism=self._unit(digest, 2),
                encouragement=self._unit(digest, 4),
                meme_affinity=self._unit(digest, 6),
                focus=(persona.traits[0] if persona.traits else persona.role),
                silence_tendency=self._unit(digest, 8),
                stay_duration_tendency=self._unit(digest, 18),
                rejoin_tendency=self._unit(digest, 20),
            ),
            joined_at_ms=created_at_ms,
            join_count=1,
            created_at_ms=created_at_ms,
        )

    @staticmethod
    def _username(digest: bytes, ordinal: int) -> str:
        prefixes = (
            "夜航",
            "像素",
            "青柠",
            "回声",
            "慢热",
            "晴窗",
            "白噪",
            "纸飞机",
            "小行星",
            "半拍",
            "路过",
            "云端",
        )
        suffixes = (
            "观测员",
            "玩家",
            "电台",
            "存档",
            "频道",
            "信号",
            "汽水",
            "耳机",
            "胶片",
            "坐标",
            "弹簧",
            "方块",
        )
        prefix = prefixes[digest[0] % len(prefixes)]
        suffix = suffixes[digest[1] % len(suffixes)]
        number = (int.from_bytes(digest[2:4], "big") + ordinal) % 100
        return f"{prefix}{suffix}{number:02d}"

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
