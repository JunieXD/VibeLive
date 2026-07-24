import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from advx_backend.infrastructure.persistence.sqlite.models import (
    ModeMemeEventRow,
    ModeMemeRow,
    RoomEventRow,
    RoomLongTermMemoryRow,
    RoomMemoryCandidateRow,
    RoomMemoryEvidenceRow,
    RoomMemoryHeadRow,
    RoomRow,
    SessionRecordRow,
    SessionRuntimeRevisionRow,
    SessionViewerInstanceRow,
)


class RuntimePersistenceConflictError(RuntimeError):
    """A compare-and-swap or idempotency precondition did not match."""


class RuntimePersistenceInvariantError(RuntimeError):
    """A cross-row runtime persistence invariant was violated."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class RuntimeRevision:
    session_id: str
    revision: int
    apply_id: str
    base_revision: int
    config_hash: str
    status: str
    canonical_spec_json: str
    diff_summary_json: str
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True)
class ViewerInstance:
    session_id: str
    viewer_instance_id: str
    persona_id: str
    persona_revision: int
    ordinal: int
    display_name: str
    micro_variant_json: str
    created_epoch: int
    removed_epoch: int | None = None
    state: str = "active"
    username: str = ""
    avatar_seed: str = ""
    color_seed: str = ""
    locale: str = "zh-CN"
    persona_content_hash: str = "0" * 64
    presence_state: str = "active"
    presence_revision: int = 1
    moderation_revision: int = 1
    behavior_revision: int = 1
    joined_at_ms: int | None = None
    last_left_at_ms: int | None = None
    join_count: int = 0
    muted_until_ms: int | None = None
    mute_reason: str | None = None
    kicked_at_ms: int | None = None
    kick_reason: str | None = None
    viewer_sequence: int = 0
    behavior_state_json: str = "{}"
    created_at_ms: int = 0
    updated_at_ms: int = 0


@dataclass(frozen=True)
class PersistedRoomEvent:
    event_id: str
    room_id: str
    session_id: str
    sequence: int
    source_type: str
    source_id: str
    audience_epoch: int
    content_json: str
    content_hash: str
    occurred_at_ms: int


@dataclass(frozen=True)
class RoomMemoryEvidence:
    event_id: str
    source_type: str
    occurred_at_ms: int
    evidence_summary: str


class SQLiteRoomRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self,
        room_id: str,
        *,
        display_name: str,
        now_ms: int,
    ) -> RoomRow:
        row = await self._session.get(RoomRow, room_id)
        if row is not None:
            return row
        row = RoomRow(
            room_id=room_id,
            display_name=display_name,
            state="active",
            revision=0,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add(
            RoomMemoryHeadRow(room_id=room_id, revision=0, updated_at_ms=now_ms)
        )
        await self._session.flush()
        return row

    async def clear(self, room_id: str) -> bool:
        result = await self._session.execute(delete(RoomRow).where(RoomRow.room_id == room_id))
        await self._session.flush()
        return result.rowcount == 1


class SQLiteSessionRuntimeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_idempotent_start(
        self,
        client_request_id: str,
        *,
        request_hash: str,
    ) -> SessionRecordRow | None:
        row = await self._session.scalar(
            select(SessionRecordRow).where(
                SessionRecordRow.client_request_id == client_request_id
            )
        )
        if row is not None and row.client_request_hash != request_hash:
            raise RuntimePersistenceConflictError(
                "client_request_id was already used with a different canonical hash"
            )
        return row

    async def start(
        self,
        *,
        session_id: str,
        room_id: str,
        client_request_id: str,
        request_hash: str,
        apply_id: str,
        canonical_spec_json: str,
        diff_summary_json: str,
        app_version: str,
        session_seed: str,
        target_concurrent_viewers: int,
        now_ms: int,
    ) -> tuple[SessionRecordRow, bool]:
        existing = await self.get_idempotent_start(
            client_request_id, request_hash=request_hash
        )
        if existing is not None:
            return existing, False
        result = await self._session.execute(
            sqlite_insert(SessionRecordRow)
            .values(
                session_id=session_id,
                room_id=room_id,
                state="starting",
                audience_epoch=0,
                active_config_hash=None,
                recovery_json=canonical_json({}),
                session_seed=session_seed,
                next_creation_ordinal=target_concurrent_viewers + 1,
                target_concurrent_viewers=target_concurrent_viewers,
                population_revision=1,
                controller_state_json=canonical_json({}),
                client_request_id=client_request_id,
                client_request_hash=request_hash,
                started_at_ms=now_ms,
                ended_at_ms=None,
                outcome=None,
                app_version=app_version,
            )
            .on_conflict_do_nothing()
        )
        if result.rowcount != 1:
            concurrent = await self.get_idempotent_start(
                client_request_id, request_hash=request_hash
            )
            if concurrent is None:
                raise RuntimePersistenceConflictError(
                    "session_id was already used by a different start request"
                )
            return concurrent, False
        record = await self._session.get(SessionRecordRow, session_id)
        if record is None:
            raise RuntimePersistenceInvariantError("inserted session record is missing")
        self._session.add(
            SessionRuntimeRevisionRow(
                session_id=session_id,
                revision=1,
                apply_id=apply_id,
                base_revision=0,
                config_hash=request_hash,
                status="pending",
                canonical_spec_json=canonical_spec_json,
                diff_summary_json=diff_summary_json,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
        )
        await self._session.flush()
        return record, True

    async def add_pending_revision(
        self,
        revision: RuntimeRevision,
    ) -> RuntimeRevision:
        existing = await self._session.scalar(
            select(SessionRuntimeRevisionRow).where(
                SessionRuntimeRevisionRow.session_id == revision.session_id,
                SessionRuntimeRevisionRow.apply_id == revision.apply_id,
            )
        )
        if existing is not None:
            if (
                existing.config_hash != revision.config_hash
                or existing.base_revision != revision.base_revision
            ):
                raise RuntimePersistenceConflictError(
                    "apply_id was already used with different revision content"
                )
            return _to_runtime_revision(existing)
        current = await self.committed_revision(revision.session_id)
        current_revision = 0 if current is None else current.revision
        if current_revision != revision.base_revision:
            raise RuntimePersistenceConflictError("runtime base revision is stale")
        self._session.add(
            SessionRuntimeRevisionRow(
                session_id=revision.session_id,
                revision=revision.revision,
                apply_id=revision.apply_id,
                base_revision=revision.base_revision,
                config_hash=revision.config_hash,
                status="pending",
                canonical_spec_json=revision.canonical_spec_json,
                diff_summary_json=revision.diff_summary_json,
                created_at_ms=revision.created_at_ms,
                updated_at_ms=revision.updated_at_ms,
            )
        )
        await self._session.flush()
        return revision

    async def committed_revision(self, session_id: str) -> RuntimeRevision | None:
        row = await self._session.scalar(
            select(SessionRuntimeRevisionRow)
            .where(
                SessionRuntimeRevisionRow.session_id == session_id,
                SessionRuntimeRevisionRow.status == "committed",
            )
            .order_by(SessionRuntimeRevisionRow.revision.desc())
            .limit(1)
        )
        return None if row is None else _to_runtime_revision(row)

    async def commit_revision(
        self,
        session_id: str,
        revision: int,
        *,
        expected_base_revision: int,
        next_epoch: int,
        expected_population_revision: int,
        next_population_revision: int,
        target_concurrent_viewers: int,
        next_creation_ordinal: int,
        now_ms: int,
        recovery: Any | None = None,
    ) -> RuntimeRevision:
        current = await self.committed_revision(session_id)
        current_revision = 0 if current is None else current.revision
        if current_revision != expected_base_revision:
            raise RuntimePersistenceConflictError("runtime base revision is stale")
        active_viewer = await self._session.scalar(
            select(SessionViewerInstanceRow.viewer_instance_id)
            .where(
                SessionViewerInstanceRow.session_id == session_id,
                SessionViewerInstanceRow.state == "active",
            )
            .limit(1)
        )
        if active_viewer is None:
            raise RuntimePersistenceInvariantError(
                "a running session requires a persisted active Viewer pool"
            )
        row = await self._session.get(SessionRuntimeRevisionRow, (session_id, revision))
        if row is None or row.status != "pending" or row.base_revision != expected_base_revision:
            raise RuntimePersistenceConflictError("pending runtime revision does not match")
        record_values: dict[str, Any] = {
            "state": "running",
            "audience_epoch": next_epoch,
            "population_revision": next_population_revision,
            "target_concurrent_viewers": target_concurrent_viewers,
            "next_creation_ordinal": func.max(
                SessionRecordRow.next_creation_ordinal,
                next_creation_ordinal,
            ),
            "active_config_hash": row.config_hash,
            "recovery_json": canonical_json({} if recovery is None else recovery),
        }
        if recovery is not None:
            record_values.update(ended_at_ms=None, outcome=None)
        result = await self._session.execute(
            update(SessionRecordRow)
            .where(
                SessionRecordRow.session_id == session_id,
                SessionRecordRow.audience_epoch < next_epoch,
                SessionRecordRow.population_revision == expected_population_revision,
            )
            .values(**record_values)
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("session epoch did not advance")
        row.status = "committed"
        row.updated_at_ms = now_ms
        await self._session.flush()
        return _to_runtime_revision(row)

    async def reject_revision(
        self,
        session_id: str,
        revision: int,
        *,
        now_ms: int,
    ) -> None:
        result = await self._session.execute(
            update(SessionRuntimeRevisionRow)
            .where(
                SessionRuntimeRevisionRow.session_id == session_id,
                SessionRuntimeRevisionRow.revision == revision,
                SessionRuntimeRevisionRow.status == "pending",
            )
            .values(status="rejected", updated_at_ms=now_ms)
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("pending runtime revision does not match")

    async def reject_orphaned_pending_revisions(
        self,
        session_id: str,
        *,
        now_ms: int,
    ) -> int:
        await self._session.execute(
            update(SessionRuntimeRevisionRow)
            .where(
                SessionRuntimeRevisionRow.session_id == session_id,
                SessionRuntimeRevisionRow.status == "pending",
            )
            .values(
                status="rejected",
                updated_at_ms=func.max(
                    SessionRuntimeRevisionRow.updated_at_ms,
                    now_ms,
                ),
            )
        )
        latest_revision = await self._session.scalar(
            select(func.max(SessionRuntimeRevisionRow.revision)).where(
                SessionRuntimeRevisionRow.session_id == session_id
            )
        )
        if latest_revision is None:
            raise RuntimePersistenceInvariantError(
                "runtime session has no persisted revisions"
            )
        return latest_revision


class SQLiteViewerInstanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_all(self, viewers: Sequence[ViewerInstance]) -> None:
        self._session.add_all(
            [
                SessionViewerInstanceRow(
                    session_id=item.session_id,
                    viewer_instance_id=item.viewer_instance_id,
                    persona_id=item.persona_id,
                    persona_revision=item.persona_revision,
                    ordinal=item.ordinal,
                    display_name=item.display_name,
                    micro_variant_json=item.micro_variant_json,
                    username=item.username,
                    avatar_seed=item.avatar_seed,
                    color_seed=item.color_seed,
                    locale=item.locale,
                    persona_content_hash=item.persona_content_hash,
                    presence_state=item.presence_state,
                    presence_revision=item.presence_revision,
                    moderation_revision=item.moderation_revision,
                    behavior_revision=item.behavior_revision,
                    joined_at_ms=item.joined_at_ms,
                    last_left_at_ms=item.last_left_at_ms,
                    join_count=item.join_count,
                    muted_until_ms=item.muted_until_ms,
                    mute_reason=item.mute_reason,
                    kicked_at_ms=item.kicked_at_ms,
                    kick_reason=item.kick_reason,
                    viewer_sequence=item.viewer_sequence,
                    behavior_state_json=item.behavior_state_json,
                    created_at_ms=item.created_at_ms,
                    updated_at_ms=item.updated_at_ms,
                    created_epoch=item.created_epoch,
                    removed_epoch=item.removed_epoch,
                    state=item.state,
                )
                for item in viewers
            ]
        )
        await self._session.flush()

    async def list_active(self, session_id: str) -> list[ViewerInstance]:
        rows = await self._session.scalars(
            select(SessionViewerInstanceRow)
            .where(
                SessionViewerInstanceRow.session_id == session_id,
                SessionViewerInstanceRow.state == "active",
            )
            .order_by(
                SessionViewerInstanceRow.persona_id,
                SessionViewerInstanceRow.ordinal,
                SessionViewerInstanceRow.viewer_instance_id,
            )
        )
        return [_to_viewer(row) for row in rows]

    async def remove(
        self,
        session_id: str,
        viewer_instance_id: str,
        *,
        removed_epoch: int,
    ) -> None:
        result = await self._session.execute(
            update(SessionViewerInstanceRow)
            .where(
                SessionViewerInstanceRow.session_id == session_id,
                SessionViewerInstanceRow.viewer_instance_id == viewer_instance_id,
                SessionViewerInstanceRow.state == "active",
            )
            .values(
                state="removed",
                presence_state="removed",
                removed_epoch=removed_epoch,
            )
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("viewer instance is missing or already removed")


class SQLiteRoomEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: PersistedRoomEvent) -> None:
        session_room = await self._session.scalar(
            select(SessionRecordRow.room_id).where(
                SessionRecordRow.session_id == event.session_id
            )
        )
        if session_room != event.room_id:
            raise RuntimePersistenceInvariantError("event room must match its session room")
        self._session.add(RoomEventRow(**event.__dict__))
        await self._session.flush()

    async def list_for_recovery(
        self,
        room_id: str,
        session_id: str,
        *,
        limit: int | None,
    ) -> list[PersistedRoomEvent]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        statement = (
            select(RoomEventRow)
            .where(
                RoomEventRow.room_id == room_id,
                RoomEventRow.session_id == session_id,
            )
            .order_by(RoomEventRow.sequence.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = await self._session.scalars(statement)
        return [_to_room_event(row) for row in reversed(list(rows))]

    async def prune(
        self,
        room_id: str,
        *,
        keep_after_ms: int,
        max_events: int,
    ) -> int:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        retained_ids = select(RoomEventRow.event_id).where(
            RoomEventRow.room_id == room_id,
            RoomEventRow.occurred_at_ms >= keep_after_ms,
        ).order_by(
            RoomEventRow.occurred_at_ms.desc(),
            RoomEventRow.event_id.desc(),
        ).limit(max_events)
        result = await self._session.execute(
            delete(RoomEventRow).where(
                RoomEventRow.room_id == room_id,
                RoomEventRow.event_id.not_in(retained_ids),
            )
        )
        return result.rowcount


class SQLiteRoomMemoryRepository:
    _NON_AI_EVIDENCE = {"user_text", "user_voice", "screen_observation", "system"}

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def head_revision(self, room_id: str) -> int:
        revision = await self._session.scalar(
            select(RoomMemoryHeadRow.revision).where(RoomMemoryHeadRow.room_id == room_id)
        )
        if revision is None:
            raise RuntimePersistenceInvariantError("room memory head is missing")
        return revision

    async def commit_candidate(
        self,
        *,
        candidate_id: str,
        room_id: str,
        idempotency_key: str,
        base_revision: int,
        candidate_type: str,
        content: str,
        tags: Sequence[str],
        memory_id: str,
        memory_origin: str,
        importance: float,
        confidence: float,
        evidence: Sequence[RoomMemoryEvidence],
        now_ms: int,
    ) -> tuple[str, int, int, bool]:
        candidate_payload = {
            "candidate_id": candidate_id,
            "room_id": room_id,
            "idempotency_key": idempotency_key,
            "base_revision": base_revision,
            "candidate_type": candidate_type,
            "content": content,
            "tags": list(tags),
            "memory_id": memory_id,
            "memory_origin": memory_origin,
            "importance": importance,
            "confidence": confidence,
            "evidence": [
                {
                    "event_id": item.event_id,
                    "source_type": item.source_type,
                    "occurred_at_ms": item.occurred_at_ms,
                    "evidence_summary": item.evidence_summary,
                }
                for item in evidence
            ],
        }
        existing = await self._session.scalar(
            select(RoomMemoryCandidateRow).where(
                RoomMemoryCandidateRow.room_id == room_id,
                RoomMemoryCandidateRow.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            try:
                decision = json.loads(existing.decision_json)
            except json.JSONDecodeError as error:
                raise RuntimePersistenceInvariantError(
                    "stored memory candidate decision is invalid"
                ) from error
            stored_candidate = decision.get("candidate")
            if not isinstance(stored_candidate, dict):
                raise RuntimePersistenceInvariantError(
                    "stored memory candidate payload is invalid"
                )
            stored_candidate = dict(stored_candidate)
            incoming_candidate = dict(candidate_payload)
            stored_candidate.pop("base_revision", None)
            incoming_candidate.pop("base_revision", None)
            if stored_candidate != incoming_candidate:
                raise RuntimePersistenceConflictError(
                    "memory idempotency key was used with a different candidate"
                )
            result_memory_id = decision.get("result_memory_id")
            memory_revision = decision.get("memory_revision")
            head_revision = decision.get("head_revision")
            if not isinstance(result_memory_id, str) or not isinstance(
                memory_revision, int
            ) or not isinstance(head_revision, int):
                raise RuntimePersistenceInvariantError(
                    "stored memory candidate result is invalid"
                )
            return result_memory_id, memory_revision, head_revision, False
        if await self.head_revision(room_id) != base_revision:
            raise RuntimePersistenceConflictError("room memory head is stale")
        if not evidence:
            raise RuntimePersistenceInvariantError("room memories require public evidence")
        event_rows = list(
            await self._session.scalars(
                select(RoomEventRow).where(
                    RoomEventRow.room_id == room_id,
                    RoomEventRow.event_id.in_([item.event_id for item in evidence]),
                )
            )
        )
        if len(event_rows) != len({item.event_id for item in evidence}):
            raise RuntimePersistenceInvariantError(
                "all memory evidence must exist in the same room"
            )
        evidence_types = {item.source_type for item in evidence}
        if candidate_type != "room_lore" and not (evidence_types & self._NON_AI_EVIDENCE):
            raise RuntimePersistenceInvariantError(
                "facts and preferences require non-AI evidence"
            )
        next_revision = base_revision + 1
        self._session.add(
            RoomLongTermMemoryRow(
                memory_id=memory_id,
                room_id=room_id,
                memory_type=candidate_type,
                content=content,
                tags_json=canonical_json(list(tags)),
                importance=importance,
                confidence=confidence,
                origin=memory_origin,
                state="active",
                superseded_by=None,
                last_recalled_at_ms=None,
                expires_at_ms=None,
                revision=1,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
        )
        await self._session.flush()
        self._session.add_all(
            [
                RoomMemoryEvidenceRow(
                    memory_id=memory_id,
                    event_id=item.event_id,
                    source_type=item.source_type,
                    occurred_at_ms=item.occurred_at_ms,
                    evidence_summary=item.evidence_summary,
                )
                for item in evidence
            ]
        )
        self._session.add(
            RoomMemoryCandidateRow(
                candidate_id=candidate_id,
                room_id=room_id,
                idempotency_key=idempotency_key,
                base_revision=base_revision,
                candidate_type=candidate_type,
                content=content,
                tags_json=canonical_json(list(tags)),
                evidence_event_ids_json=canonical_json([item.event_id for item in evidence]),
                outcome="created",
                result_memory_id=memory_id,
                decision_json=canonical_json(
                    {
                        "decision": "created",
                        "candidate": candidate_payload,
                        "result_memory_id": memory_id,
                        "memory_revision": 1,
                        "head_revision": next_revision,
                    }
                ),
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
        )
        result = await self._session.execute(
            update(RoomMemoryHeadRow)
            .where(
                RoomMemoryHeadRow.room_id == room_id,
                RoomMemoryHeadRow.revision == base_revision,
            )
            .values(revision=next_revision, updated_at_ms=now_ms)
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("room memory head is stale")
        await self._session.execute(
            update(RoomRow)
            .where(RoomRow.room_id == room_id, RoomRow.revision == base_revision)
            .values(revision=next_revision, updated_at_ms=now_ms)
        )
        await self._session.flush()
        return memory_id, 1, next_revision, True

    async def delete(self, room_id: str, memory_id: str) -> bool:
        result = await self._session.execute(
            delete(RoomLongTermMemoryRow).where(
                RoomLongTermMemoryRow.room_id == room_id,
                RoomLongTermMemoryRow.memory_id == memory_id,
            )
        )
        await self._session.flush()
        return result.rowcount == 1


class SQLiteModeMemeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        meme_id: str,
        event_id: str,
        mode_namespace: str,
        content: str,
        intensity: float,
        source: Any,
        now_ms: int,
    ) -> ModeMemeRow:
        row = ModeMemeRow(
            meme_id=meme_id,
            mode_namespace=mode_namespace,
            content=content,
            intensity=intensity,
            state="active",
            source_json=canonical_json(source),
            revision=1,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add(
            ModeMemeEventRow(
                event_id=event_id,
                meme_id=meme_id,
                action="created",
                payload_json=canonical_json({"content": content}),
                previous_revision=0,
                new_revision=1,
                created_at_ms=now_ms,
            )
        )
        await self._session.flush()
        return row

    async def change_state(
        self,
        meme_id: str,
        *,
        event_id: str,
        expected_revision: int,
        state: str,
        action: str,
        now_ms: int,
    ) -> int:
        next_revision = expected_revision + 1
        result = await self._session.execute(
            update(ModeMemeRow)
            .where(
                ModeMemeRow.meme_id == meme_id,
                ModeMemeRow.revision == expected_revision,
            )
            .values(state=state, revision=next_revision, updated_at_ms=now_ms)
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("mode meme revision is stale")
        self._session.add(
            ModeMemeEventRow(
                event_id=event_id,
                meme_id=meme_id,
                action=action,
                payload_json=canonical_json({"state": state}),
                previous_revision=expected_revision,
                new_revision=next_revision,
                created_at_ms=now_ms,
            )
        )
        await self._session.flush()
        return next_revision


def _to_runtime_revision(row: SessionRuntimeRevisionRow) -> RuntimeRevision:
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


def _to_viewer(row: SessionViewerInstanceRow) -> ViewerInstance:
    return ViewerInstance(
        session_id=row.session_id,
        viewer_instance_id=row.viewer_instance_id,
        persona_id=row.persona_id,
        persona_revision=row.persona_revision,
        ordinal=row.ordinal,
        display_name=row.display_name,
        micro_variant_json=row.micro_variant_json,
        created_epoch=row.created_epoch,
        removed_epoch=row.removed_epoch,
        state=row.state,
        username=row.username,
        avatar_seed=row.avatar_seed,
        color_seed=row.color_seed,
        locale=row.locale,
        persona_content_hash=row.persona_content_hash,
        presence_state=row.presence_state,
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
        viewer_sequence=row.viewer_sequence,
        behavior_state_json=row.behavior_state_json,
        created_at_ms=row.created_at_ms,
        updated_at_ms=row.updated_at_ms,
    )


def _to_room_event(row: RoomEventRow) -> PersistedRoomEvent:
    return PersistedRoomEvent(
        event_id=row.event_id,
        room_id=row.room_id,
        session_id=row.session_id,
        sequence=row.sequence,
        source_type=row.source_type,
        source_id=row.source_id,
        audience_epoch=row.audience_epoch,
        content_json=row.content_json,
        content_hash=row.content_hash,
        occurred_at_ms=row.occurred_at_ms,
    )
