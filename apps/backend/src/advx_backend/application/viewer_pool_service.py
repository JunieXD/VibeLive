from __future__ import annotations

import hashlib
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

_GIVEN_NAMES = (
    "阿北",
    "阿沐",
    "小陈",
    "小林",
    "阿澈",
    "桃子",
    "柚子",
    "小七",
    "小满",
    "阿禾",
    "南风",
    "可乐",
)
_NICKNAMES = (
    "土豆",
    "青柠",
    "泡芙",
    "栗子",
    "团子",
    "番茄",
    "豆花",
    "布丁",
    "年糕",
    "汽水",
    "键帽",
    "耳机",
)
_STATES = (
    "熬夜",
    "路过",
    "排队",
    "潜水",
    "摸鱼",
    "掉线",
    "手慢",
    "蹲点",
    "观战",
    "等开局",
    "看回放",
    "刚上线",
)
_GAME_WORDS = (
    "排位",
    "残局",
    "补枪",
    "守点",
    "烟雾",
    "压枪",
    "爆头",
    "开麦",
    "观战",
    "练枪",
    "上分",
    "回防",
)
_ROLES = (
    "练习生",
    "研究员",
    "观察员",
    "路人",
    "替补",
    "记录员",
    "队友",
    "摸鱼员",
    "气氛组",
    "小助手",
    "爱好者",
    "玩家",
)
_OBJECTS = (
    "耳机",
    "键盘",
    "鼠标",
    "手柄",
    "汽水",
    "外设",
    "显示器",
    "弹幕",
    "盒饭",
    "键帽",
    "背包",
    "充电线",
)
_HANDLES = (
    "momo",
    "nono",
    "kira",
    "vivi",
    "zero",
    "mika",
    "niko",
    "mimi",
    "yoyo",
    "kiwi",
    "sora",
    "nana",
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
    """Compile a deterministic Viewer pool with exact per-Persona counts."""

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
        counts = self._counts(mode, personas)
        persona_slots = self._persona_slots(
            mode=mode,
            counts=counts,
            session_seed=session_seed,
        )
        viewers: list[ViewerInstance] = []
        usernames: set[str] = set()
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
                    used_usernames=usernames,
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
        mode = self._active_mode(spec)
        desired_counts = self._counts(mode, next_personas)
        target_count = sum(desired_counts.values())
        active_viewers = [
            viewer
            for viewer in sorted(current.viewers, key=lambda item: item.ordinal)
            if viewer.is_active()
        ]
        remaining_counts = dict(desired_counts)
        retained_active_ids: set[str] = set()
        for viewer in active_viewers:
            if remaining_counts.get(viewer.persona_id, 0) <= 0:
                continue
            retained_active_ids.add(viewer.viewer_instance_id)
            remaining_counts[viewer.persona_id] -= 1

        reassignment_slots = self._persona_slots(
            mode=mode,
            counts=remaining_counts,
            session_seed=f"{current.session_seed}\0reconcile\0{next_epoch}",
        )
        reassignment_ids = [
            viewer.viewer_instance_id
            for viewer in active_viewers
            if viewer.viewer_instance_id not in retained_active_ids
        ]
        assignments = dict(zip(reassignment_ids, reassignment_slots, strict=False))
        removed_active_ids = set(reassignment_ids[len(reassignment_slots) :])

        reconciled: list[ViewerInstance] = []
        retained: list[str] = []
        reset: list[str] = []
        added: list[str] = []
        removed: list[str] = []
        for previous in sorted(current.viewers, key=lambda item: item.ordinal):
            if previous.viewer_instance_id in removed_active_ids:
                removed.append(previous.viewer_instance_id)
                continue

            assigned_persona_id = assignments.get(previous.viewer_instance_id)
            next_persona = next_personas.get(assigned_persona_id or previous.persona_id)
            if next_persona is None:
                viewer = previous.model_copy(update={"audience_epoch": next_epoch})
                reconciled.append(viewer)
                retained.append(viewer.viewer_instance_id)
                continue
            persona_changed = next_persona.persona_id != previous.persona_id
            unchanged = (
                not persona_changed
                and previous_signatures.get(previous.persona_id)
                == next_signatures.get(previous.persona_id)
            )
            viewer = previous.model_copy(
                update={
                    "audience_epoch": next_epoch,
                    "persona_id": next_persona.persona_id,
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
                    "private_state": previous.private_state if unchanged else ViewerPrivateState(),
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
        personas = {
            persona.persona_id: persona
            for persona in spec.personas
            if persona.enabled
        }
        desired_counts = self._counts(mode, personas)
        active_counts = {
            persona_id: sum(
                viewer.is_active() and viewer.persona_id == persona_id
                for viewer in current.viewers
            )
            for persona_id in desired_counts
        }
        deficits = {
            persona_id: count - active_counts[persona_id]
            for persona_id, count in desired_counts.items()
            if count > active_counts[persona_id]
        }
        if not deficits:
            raise ValueError("No persona slot is available for a replacement Viewer")
        slots = self._persona_slots(
            mode=mode,
            counts=deficits,
            session_seed=f"{current.session_seed}\0replacement\0{ordinal}",
        )
        persona = personas[slots[0]]
        return self._new_viewer(
            room_id=current.room_id,
            session_id=current.session_id,
            audience_epoch=current.audience_epoch,
            session_seed=current.session_seed,
            persona=persona,
            ordinal=ordinal,
            created_at_ms=created_at_ms,
            used_usernames={viewer.username for viewer in current.viewers},
        )

    @staticmethod
    def _active_mode(spec: CanonicalRuntimeSpec) -> ModeDefinition:
        return next(mode for mode in spec.modes if mode.mode_id == spec.active_mode_id)

    @staticmethod
    def _counts(
        mode: ModeDefinition,
        personas: Mapping[str, PersonaTemplate],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for persona_id, count in mode.persona_counts.items():
            if count == 0:
                continue
            persona = personas.get(persona_id)
            if persona is None or not persona.enabled:
                raise ValueError("Mode contains an unavailable Persona with a positive count")
            counts[persona_id] = count
        if not counts:
            raise ValueError("Mode does not contain an enabled Persona with a positive count")
        return counts

    @staticmethod
    def _persona_slots(
        *,
        mode: ModeDefinition,
        counts: Mapping[str, int],
        session_seed: str,
    ) -> list[str]:
        slots = [
            (persona_id, ordinal)
            for persona_id, count in counts.items()
            for ordinal in range(1, count + 1)
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
        used_usernames: set[str],
    ) -> ViewerInstance:
        seed = f"{session_seed}\x00{session_id}\x00{ordinal}\x00viewer-v2"
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        username = self._unique_username(self._username(digest, ordinal), used_usernames)
        used_usernames.add(username)
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
        template = digest[0] % 6
        if template == 0:
            return _GIVEN_NAMES[digest[1] % len(_GIVEN_NAMES)]
        if template == 1:
            return f"小{_NICKNAMES[digest[1] % len(_NICKNAMES)]}"
        if template == 2:
            return (
                f"{_STATES[digest[1] % len(_STATES)]}"
                f"{_ROLES[digest[2] % len(_ROLES)]}"
            )
        if template == 3:
            return (
                f"{_GAME_WORDS[digest[1] % len(_GAME_WORDS)]}"
                f"{_ROLES[digest[2] % len(_ROLES)]}"
            )
        if template == 4:
            return (
                f"{_GIVEN_NAMES[digest[1] % len(_GIVEN_NAMES)]}的"
                f"{_OBJECTS[digest[2] % len(_OBJECTS)]}"
            )
        number = (int.from_bytes(digest[3:5], "big") + ordinal) % 100
        return f"{_HANDLES[digest[1] % len(_HANDLES)]}_{number:02d}"

    @staticmethod
    def _unique_username(username: str, used_usernames: set[str]) -> str:
        if username not in used_usernames:
            return username
        suffix = 2
        while f"{username}_{suffix}" in used_usernames:
            suffix += 1
        return f"{username}_{suffix}"

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
