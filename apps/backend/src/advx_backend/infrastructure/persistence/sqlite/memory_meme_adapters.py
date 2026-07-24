import json
import time
from collections.abc import Sequence

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from advx_backend.application.ports.meme import (
    MemeAutoIngestSetting,
    MemeCommitResult,
)
from advx_backend.application.ports.memory import (
    MemoryCommitResult,
    MemoryEvidence,
    RoomMemoryCandidate,
)
from advx_backend.domain.meme import (
    MemeCandidate,
    MemeCandidateOutcome,
    ModeMeme,
    ModeMemeState,
)
from advx_backend.domain.memory import RoomLongTermMemory, RoomMemorySlice, RoomMemoryType
from advx_backend.infrastructure.persistence.sqlite.models import (
    ModeMemeCandidateRow,
    ModeMemeEventRow,
    ModeMemeRow,
    ModeMemeSettingRow,
    RoomEventRow,
    RoomLongTermMemoryRow,
    RoomMemoryEvidenceRow,
    RoomMemoryHeadRow,
    RoomRow,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    RoomMemoryEvidence as PersistedMemoryEvidence,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    RuntimePersistenceConflictError,
    RuntimePersistenceInvariantError,
    SQLiteModeMemeRepository,
    SQLiteRoomMemoryRepository,
    canonical_json,
)


class SQLiteRoomEventReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_events(
        self,
        event_ids: tuple[str, ...],
    ) -> tuple[MemoryEvidence, ...]:
        if not event_ids:
            return ()
        rows = list(
            await self._session.scalars(
                select(RoomEventRow).where(RoomEventRow.event_id.in_(event_ids))
            )
        )
        by_id = {row.event_id: row for row in rows}
        return tuple(
            MemoryEvidence(
                event_id=row.event_id,
                room_id=row.room_id,
                source_type=row.source_type,
                occurred_at_ms=row.occurred_at_ms,
                summary=row.content_json,
            )
            for event_id in event_ids
            if (row := by_id.get(event_id)) is not None
        )


class SQLiteRoomMemoryServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SQLiteRoomMemoryRepository(session)

    async def head_revision(self, room_id: str) -> int:
        return await self._repository.head_revision(room_id)

    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice:
        revision = await self._repository.head_revision(room_id)
        now_ms = int(time.time() * 1_000)
        conditions = [
            RoomLongTermMemoryRow.room_id == room_id,
            RoomLongTermMemoryRow.state == "active",
            or_(
                RoomLongTermMemoryRow.expires_at_ms.is_(None),
                RoomLongTermMemoryRow.expires_at_ms > now_ms,
            ),
        ]
        if event_ids:
            conditions.append(
                exists().where(
                    RoomMemoryEvidenceRow.memory_id
                    == RoomLongTermMemoryRow.memory_id,
                    RoomMemoryEvidenceRow.event_id.in_(event_ids),
                )
            )
        rows = list(
            await self._session.scalars(
                select(RoomLongTermMemoryRow)
                .where(*conditions)
                .order_by(
                    RoomLongTermMemoryRow.importance.desc(),
                    RoomLongTermMemoryRow.updated_at_ms.desc(),
                    RoomLongTermMemoryRow.memory_id,
                )
                .limit(limit)
            )
        )
        evidence = await self._evidence_by_memory(row.memory_id for row in rows)
        items = [
            _to_memory(row, evidence.get(row.memory_id, ()))
            for row in rows
        ]
        return RoomMemorySlice(
            room_id=room_id,
            memory_revision=revision,
            memory_ids=[item.memory_id for item in items],
            items=items,
        )

    async def commit_candidate(
        self,
        candidate: RoomMemoryCandidate,
        *,
        evidence: Sequence[MemoryEvidence],
        now_ms: int,
    ) -> MemoryCommitResult:
        memory_id, memory_revision, head_revision, created = (
            await self._repository.commit_candidate(
            candidate_id=candidate.candidate_id,
            room_id=candidate.room_id,
            idempotency_key=candidate.idempotency_key,
            base_revision=candidate.base_revision,
            candidate_type=candidate.memory_type.value,
            content=candidate.content,
            tags=candidate.tags,
            memory_id=candidate.memory_id,
            memory_origin=candidate.origin,
            importance=candidate.importance,
            confidence=candidate.confidence,
            evidence=[
                PersistedMemoryEvidence(
                    event_id=item.event_id,
                    source_type=item.source_type,
                    occurred_at_ms=item.occurred_at_ms,
                    evidence_summary=item.summary,
                )
                for item in evidence
            ],
            now_ms=now_ms,
            )
        )
        return MemoryCommitResult(
            accepted=True,
            memory_id=memory_id,
            memory_revision=memory_revision,
            head_revision=head_revision,
            created=created,
        )

    async def list_active(self, room_id: str) -> tuple[RoomLongTermMemory, ...]:
        now_ms = int(time.time() * 1_000)
        rows = list(
            await self._session.scalars(
                select(RoomLongTermMemoryRow)
                .where(
                    RoomLongTermMemoryRow.room_id == room_id,
                    RoomLongTermMemoryRow.state == "active",
                    or_(
                        RoomLongTermMemoryRow.expires_at_ms.is_(None),
                        RoomLongTermMemoryRow.expires_at_ms > now_ms,
                    ),
                )
                .order_by(
                    RoomLongTermMemoryRow.updated_at_ms.desc(),
                    RoomLongTermMemoryRow.memory_id,
                )
            )
        )
        evidence = await self._evidence_by_memory(row.memory_id for row in rows)
        return tuple(_to_memory(row, evidence.get(row.memory_id, ())) for row in rows)

    async def get(self, room_id: str, memory_id: str) -> RoomLongTermMemory:
        row = await self._require_memory(room_id, memory_id)
        await self._session.refresh(row)
        evidence = await self._evidence_by_memory((memory_id,))
        return _to_memory(row, evidence.get(memory_id, ()))

    async def edit(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        content: str,
        confidence: float,
        evidence_event_ids: tuple[str, ...],
        now_ms: int,
    ) -> RoomLongTermMemory:
        row = await self._require_memory(room_id, memory_id)
        if row.revision != expected_revision:
            raise RuntimePersistenceConflictError("room memory revision is stale")
        evidence_rows = await self._validated_events(room_id, evidence_event_ids)
        head_revision = await self._repository.head_revision(room_id)
        result = await self._session.execute(
            update(RoomLongTermMemoryRow)
            .where(
                RoomLongTermMemoryRow.memory_id == memory_id,
                RoomLongTermMemoryRow.room_id == room_id,
                RoomLongTermMemoryRow.revision == expected_revision,
            )
            .values(
                content=content,
                confidence=confidence,
                revision=expected_revision + 1,
                updated_at_ms=now_ms,
            )
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("room memory revision is stale")
        await self._session.execute(
            delete(RoomMemoryEvidenceRow).where(
                RoomMemoryEvidenceRow.memory_id == memory_id
            )
        )
        self._session.add_all(
            [
                RoomMemoryEvidenceRow(
                    memory_id=memory_id,
                    event_id=event.event_id,
                    source_type=event.source_type,
                    occurred_at_ms=event.occurred_at_ms,
                    evidence_summary=event.content_json[:1_000],
                )
                for event in evidence_rows
            ]
        )
        await self._advance_head(room_id, head_revision, now_ms)
        return await self.get(room_id, memory_id)

    async def merge(
        self,
        room_id: str,
        memory_id: str,
        source_memory_id: str,
        *,
        expected_revision: int,
        source_expected_revision: int,
        content: str,
        now_ms: int,
    ) -> RoomLongTermMemory:
        if memory_id == source_memory_id:
            raise RuntimePersistenceInvariantError("memory cannot be merged into itself")
        target = await self._require_memory(room_id, memory_id)
        source = await self._require_memory(room_id, source_memory_id)
        if (
            target.revision != expected_revision
            or source.revision != source_expected_revision
            or target.state != "active"
            or source.state != "active"
        ):
            raise RuntimePersistenceConflictError("room memory revision is stale")
        head_revision = await self._repository.head_revision(room_id)
        target_result = await self._session.execute(
            update(RoomLongTermMemoryRow)
            .where(
                RoomLongTermMemoryRow.memory_id == memory_id,
                RoomLongTermMemoryRow.revision == expected_revision,
                RoomLongTermMemoryRow.state == "active",
            )
            .values(
                content=content,
                revision=expected_revision + 1,
                updated_at_ms=now_ms,
            )
        )
        source_result = await self._session.execute(
            update(RoomLongTermMemoryRow)
            .where(
                RoomLongTermMemoryRow.memory_id == source_memory_id,
                RoomLongTermMemoryRow.revision == source_expected_revision,
                RoomLongTermMemoryRow.state == "active",
            )
            .values(
                state="superseded",
                superseded_by=memory_id,
                revision=source_expected_revision + 1,
                updated_at_ms=now_ms,
            )
        )
        if target_result.rowcount != 1 or source_result.rowcount != 1:
            raise RuntimePersistenceConflictError("room memory revision is stale")
        existing_ids = set((await self._evidence_by_memory((memory_id,))).get(memory_id, ()))
        source_evidence = list(
            await self._session.scalars(
                select(RoomMemoryEvidenceRow).where(
                    RoomMemoryEvidenceRow.memory_id == source_memory_id
                )
            )
        )
        self._session.add_all(
            [
                RoomMemoryEvidenceRow(
                    memory_id=memory_id,
                    event_id=item.event_id,
                    source_type=item.source_type,
                    occurred_at_ms=item.occurred_at_ms,
                    evidence_summary=item.evidence_summary,
                )
                for item in source_evidence
                if item.event_id not in existing_ids
            ]
        )
        await self._advance_head(room_id, head_revision, now_ms)
        return await self.get(room_id, memory_id)

    async def replace(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        replacement_memory_id: str,
        content: str,
        evidence_event_ids: tuple[str, ...],
        now_ms: int,
    ) -> RoomLongTermMemory:
        current = await self._require_memory(room_id, memory_id)
        if current.revision != expected_revision or current.state != "active":
            raise RuntimePersistenceConflictError("room memory revision is stale")
        if await self._session.get(RoomLongTermMemoryRow, replacement_memory_id) is not None:
            raise RuntimePersistenceConflictError("replacement memory id already exists")
        event_rows = await self._validated_events(room_id, evidence_event_ids)
        head_revision = await self._repository.head_revision(room_id)
        self._session.add(
            RoomLongTermMemoryRow(
                memory_id=replacement_memory_id,
                room_id=room_id,
                memory_type=current.memory_type,
                content=content,
                tags_json=current.tags_json,
                importance=current.importance,
                confidence=current.confidence,
                origin="manual_replace",
                state="active",
                superseded_by=None,
                last_recalled_at_ms=None,
                expires_at_ms=current.expires_at_ms,
                revision=1,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
        )
        await self._session.flush()
        self._session.add_all(
            [
                RoomMemoryEvidenceRow(
                    memory_id=replacement_memory_id,
                    event_id=event.event_id,
                    source_type=event.source_type,
                    occurred_at_ms=event.occurred_at_ms,
                    evidence_summary=event.content_json[:1_000],
                )
                for event in event_rows
            ]
        )
        result = await self._session.execute(
            update(RoomLongTermMemoryRow)
            .where(
                RoomLongTermMemoryRow.memory_id == memory_id,
                RoomLongTermMemoryRow.revision == expected_revision,
                RoomLongTermMemoryRow.state == "active",
            )
            .values(
                state="superseded",
                superseded_by=replacement_memory_id,
                revision=expected_revision + 1,
                updated_at_ms=now_ms,
            )
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("room memory revision is stale")
        await self._advance_head(room_id, head_revision, now_ms)
        return await self.get(room_id, replacement_memory_id)

    async def revoke(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> RoomLongTermMemory:
        head_revision = await self._repository.head_revision(room_id)
        result = await self._session.execute(
            update(RoomLongTermMemoryRow)
            .where(
                RoomLongTermMemoryRow.room_id == room_id,
                RoomLongTermMemoryRow.memory_id == memory_id,
                RoomLongTermMemoryRow.revision == expected_revision,
                RoomLongTermMemoryRow.state == "active",
            )
            .values(
                state="revoked",
                revision=expected_revision + 1,
                updated_at_ms=now_ms,
            )
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("room memory revision is stale")
        await self._advance_head(room_id, head_revision, now_ms)
        row = await self._session.get(RoomLongTermMemoryRow, memory_id)
        if row is None:
            raise RuntimePersistenceInvariantError("revoked room memory is missing")
        evidence = await self._evidence_by_memory((memory_id,))
        return _to_memory(row, evidence.get(memory_id, ()))

    async def delete(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> bool:
        head_revision = await self._repository.head_revision(room_id)
        result = await self._session.execute(
            delete(RoomLongTermMemoryRow).where(
                RoomLongTermMemoryRow.room_id == room_id,
                RoomLongTermMemoryRow.memory_id == memory_id,
                RoomLongTermMemoryRow.revision == expected_revision,
            )
        )
        if result.rowcount != 1:
            exists = await self._session.scalar(
                select(RoomLongTermMemoryRow.memory_id).where(
                    RoomLongTermMemoryRow.room_id == room_id,
                    RoomLongTermMemoryRow.memory_id == memory_id,
                )
            )
            if exists is not None:
                raise RuntimePersistenceConflictError("room memory revision is stale")
            return False
        await self._advance_head(room_id, head_revision, now_ms)
        return True

    async def reset(
        self,
        room_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> int:
        head_revision = await self._repository.head_revision(room_id)
        if head_revision != expected_revision:
            raise RuntimePersistenceConflictError("room memory head is stale")
        result = await self._session.execute(
            delete(RoomLongTermMemoryRow).where(RoomLongTermMemoryRow.room_id == room_id)
        )
        count = result.rowcount
        if count:
            await self._advance_head(room_id, head_revision, now_ms)
        return count

    async def _advance_head(
        self,
        room_id: str,
        expected_revision: int,
        now_ms: int,
    ) -> None:
        next_revision = expected_revision + 1
        result = await self._session.execute(
            update(RoomMemoryHeadRow)
            .where(
                RoomMemoryHeadRow.room_id == room_id,
                RoomMemoryHeadRow.revision == expected_revision,
            )
            .values(revision=next_revision, updated_at_ms=now_ms)
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("room memory head is stale")
        await self._session.execute(
            update(RoomRow)
            .where(RoomRow.room_id == room_id, RoomRow.revision == expected_revision)
            .values(revision=next_revision, updated_at_ms=now_ms)
        )
        await self._session.flush()

    async def _require_memory(
        self,
        room_id: str,
        memory_id: str,
    ) -> RoomLongTermMemoryRow:
        row = await self._session.get(RoomLongTermMemoryRow, memory_id)
        if row is None or row.room_id != room_id:
            raise RuntimePersistenceInvariantError("room memory is missing")
        return row

    async def _validated_events(
        self,
        room_id: str,
        event_ids: tuple[str, ...],
    ) -> list[RoomEventRow]:
        if not event_ids:
            raise RuntimePersistenceInvariantError("room memories require public evidence")
        rows = list(
            await self._session.scalars(
                select(RoomEventRow).where(
                    RoomEventRow.room_id == room_id,
                    RoomEventRow.event_id.in_(event_ids),
                )
            )
        )
        if len(rows) != len(set(event_ids)):
            raise RuntimePersistenceInvariantError(
                "all memory evidence must exist in the same room"
            )
        return rows

    async def _evidence_by_memory(
        self,
        memory_ids: Sequence[str],
    ) -> dict[str, tuple[str, ...]]:
        ids = tuple(memory_ids)
        if not ids:
            return {}
        rows = list(
            await self._session.scalars(
                select(RoomMemoryEvidenceRow)
                .where(RoomMemoryEvidenceRow.memory_id.in_(ids))
                .order_by(RoomMemoryEvidenceRow.memory_id, RoomMemoryEvidenceRow.event_id)
            )
        )
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row.memory_id, []).append(row.event_id)
        return {memory_id: tuple(event_ids) for memory_id, event_ids in grouped.items()}


class SQLiteModeMemeServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SQLiteModeMemeRepository(session)

    async def list_active(self, namespace_id: str) -> tuple[ModeMeme, ...]:
        rows = list(
            await self._session.scalars(
                select(ModeMemeRow)
                .where(
                    ModeMemeRow.mode_namespace == namespace_id,
                    ModeMemeRow.state == ModeMemeState.ACTIVE.value,
                )
                .order_by(ModeMemeRow.updated_at_ms.desc(), ModeMemeRow.meme_id)
            )
        )
        return tuple(_to_meme(row) for row in rows)

    async def save_candidate(self, candidate: MemeCandidate) -> None:
        await self._validate_idempotency(candidate)
        existing = await self._session.get(ModeMemeCandidateRow, candidate.candidate_id)
        if existing is not None:
            self._validate_candidate(existing, candidate)
            return
        self._session.add(_candidate_row(candidate, outcome="pending"))
        await self._session.flush()

    async def list_pending(self, namespace_id: str) -> tuple[MemeCandidate, ...]:
        rows = list(
            await self._session.scalars(
                select(ModeMemeCandidateRow)
                .where(
                    ModeMemeCandidateRow.mode_namespace == namespace_id,
                    ModeMemeCandidateRow.outcome == "pending",
                )
                .order_by(
                    ModeMemeCandidateRow.created_at_ms,
                    ModeMemeCandidateRow.candidate_id,
                )
            )
        )
        return tuple(_to_candidate(row) for row in rows)

    async def get_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
    ) -> MemeCandidate:
        row = await self._session.get(ModeMemeCandidateRow, candidate_id)
        if row is None or row.mode_namespace != namespace_id:
            raise RuntimePersistenceInvariantError("meme candidate is missing")
        return _to_candidate(row)

    async def find_candidate(
        self,
        candidate_id: str,
    ) -> MemeCandidate | None:
        row = await self._session.get(ModeMemeCandidateRow, candidate_id)
        return None if row is None else _to_candidate(row)

    async def get_auto_ingest(self, namespace_id: str) -> MemeAutoIngestSetting:
        row = await self._session.get(ModeMemeSettingRow, namespace_id)
        if row is None:
            return MemeAutoIngestSetting(
                namespace_id=namespace_id,
                enabled=True,
                revision=0,
            )
        return _to_auto_ingest_setting(row)

    async def set_auto_ingest(
        self,
        namespace_id: str,
        *,
        enabled: bool,
        expected_revision: int,
        now_ms: int,
    ) -> MemeAutoIngestSetting:
        if expected_revision == 0:
            result = await self._session.execute(
                sqlite_insert(ModeMemeSettingRow)
                .values(
                    mode_namespace=namespace_id,
                    auto_ingest_enabled=enabled,
                    revision=1,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
                .on_conflict_do_nothing()
            )
            if result.rowcount != 1:
                raise RuntimePersistenceConflictError(
                    "mode meme setting revision is stale"
                )
        else:
            result = await self._session.execute(
                update(ModeMemeSettingRow)
                .where(
                    ModeMemeSettingRow.mode_namespace == namespace_id,
                    ModeMemeSettingRow.revision == expected_revision,
                )
                .values(
                    auto_ingest_enabled=enabled,
                    revision=expected_revision + 1,
                    updated_at_ms=now_ms,
                )
            )
            if result.rowcount != 1:
                raise RuntimePersistenceConflictError(
                    "mode meme setting revision is stale"
                )
        await self._session.flush()
        row = await self._session.get(ModeMemeSettingRow, namespace_id)
        if row is None:
            raise RuntimePersistenceInvariantError("mode meme setting is missing")
        await self._session.refresh(row)
        return _to_auto_ingest_setting(row)

    async def commit_candidate(self, candidate: MemeCandidate) -> MemeCommitResult:
        await self._validate_idempotency(candidate)
        meme_id = f"meme:{candidate.candidate_id}"
        candidate_row = await self._session.get(
            ModeMemeCandidateRow,
            candidate.candidate_id,
        )
        if candidate_row is not None:
            self._validate_candidate(candidate_row, candidate)
            if candidate_row.outcome == "accepted":
                return MemeCommitResult(
                    accepted=True,
                    meme_id=candidate_row.result_meme_id or meme_id,
                )
        existing = await self._session.get(ModeMemeRow, meme_id)
        if existing is not None:
            source = _source(existing)
            if (
                existing.mode_namespace != candidate.namespace_id
                or existing.content != candidate.text
                or source.get("source_candidate_id") != candidate.candidate_id
            ):
                raise RuntimePersistenceConflictError(
                    "meme candidate id was used with different content"
                )
            return MemeCommitResult(accepted=True, meme_id=meme_id)

        await self._repository.create(
            meme_id=meme_id,
            event_id=f"meme-event:created:{candidate.candidate_id}",
            mode_namespace=candidate.namespace_id,
            content=candidate.text,
            intensity=0.5,
            source={
                "room_id": candidate.room_id,
                "session_id": candidate.session_id,
                "audience_epoch": candidate.audience_epoch,
                "observation_id": candidate.observation_id,
                "source_candidate_id": candidate.candidate_id,
                "evidence_event_ids": candidate.evidence_event_ids,
                "evidence_frame_indexes": candidate.evidence_frame_indexes,
                "pinned": False,
                "use_count": 0,
                "last_used_at_ms": None,
            },
            now_ms=candidate.created_at_ms,
        )
        if candidate_row is None:
            candidate_row = _candidate_row(
                candidate,
                outcome="accepted",
                result_meme_id=meme_id,
            )
            self._session.add(candidate_row)
        else:
            result = await self._session.execute(
                update(ModeMemeCandidateRow)
                .where(
                    ModeMemeCandidateRow.candidate_id == candidate.candidate_id,
                    ModeMemeCandidateRow.outcome == "pending",
                )
                .values(
                    outcome="accepted",
                    result_meme_id=meme_id,
                    updated_at_ms=candidate.created_at_ms,
                )
            )
            if result.rowcount != 1:
                raise RuntimePersistenceConflictError(
                    "meme candidate outcome is stale"
                )
        await self._session.flush()
        return MemeCommitResult(accepted=True, meme_id=meme_id)

    async def approve_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
        *,
        now_ms: int,
    ) -> MemeCommitResult:
        row = await self._session.get(ModeMemeCandidateRow, candidate_id)
        if (
            row is None
            or row.mode_namespace != namespace_id
            or row.outcome != "pending"
        ):
            raise RuntimePersistenceConflictError("meme candidate is not pending")
        candidate = _to_candidate(row).model_copy(update={"created_at_ms": now_ms})
        return await self.commit_candidate(candidate)

    async def reject_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
        *,
        now_ms: int,
    ) -> MemeCandidate:
        result = await self._session.execute(
            update(ModeMemeCandidateRow)
            .where(
                ModeMemeCandidateRow.candidate_id == candidate_id,
                ModeMemeCandidateRow.mode_namespace == namespace_id,
                ModeMemeCandidateRow.outcome == "pending",
            )
            .values(outcome="rejected", updated_at_ms=now_ms)
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("meme candidate is not pending")
        row = await self._session.get(ModeMemeCandidateRow, candidate_id)
        if row is None:
            raise RuntimePersistenceInvariantError("meme candidate is missing")
        await self._session.refresh(row)
        return _to_candidate(row)

    async def list_all(self, namespace_id: str) -> tuple[ModeMeme, ...]:
        rows = list(
            await self._session.scalars(
                select(ModeMemeRow)
                .where(ModeMemeRow.mode_namespace == namespace_id)
                .order_by(ModeMemeRow.updated_at_ms.desc(), ModeMemeRow.meme_id)
            )
        )
        return tuple(_to_meme(row) for row in rows)

    async def get(self, meme_id: str) -> ModeMeme:
        return await self._get_meme(meme_id)

    async def edit(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        text: str,
        intensity: float,
        now_ms: int,
    ) -> ModeMeme:
        next_revision = expected_revision + 1
        result = await self._session.execute(
            update(ModeMemeRow)
            .where(
                ModeMemeRow.meme_id == meme_id,
                ModeMemeRow.revision == expected_revision,
            )
            .values(
                content=text,
                intensity=intensity,
                revision=next_revision,
                updated_at_ms=now_ms,
            )
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("mode meme revision is stale")
        self._session.add(
            ModeMemeEventRow(
                event_id=_meme_event_id(meme_id, "edited", next_revision),
                meme_id=meme_id,
                action="edited",
                payload_json=canonical_json(
                    {"text": text, "intensity": intensity}
                ),
                previous_revision=expected_revision,
                new_revision=next_revision,
                created_at_ms=now_ms,
            )
        )
        await self._session.flush()
        return await self._get_meme(meme_id)

    async def change_state(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        state: ModeMemeState,
        action: str,
        now_ms: int,
    ) -> ModeMeme:
        await self._repository.change_state(
            meme_id,
            event_id=_meme_event_id(meme_id, action, expected_revision + 1),
            expected_revision=expected_revision,
            state=state.value,
            action=action,
            now_ms=now_ms,
        )
        return await self._get_meme(meme_id)

    async def set_pinned(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        pinned: bool,
        now_ms: int,
    ) -> ModeMeme:
        row = await self._require_revision(meme_id, expected_revision)
        source = _source(row)
        source["pinned"] = pinned
        await self._update_source(
            row,
            source,
            action="edited",
            payload={"pinned": pinned},
            now_ms=now_ms,
        )
        return _to_meme(row)

    async def record_use(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> ModeMeme:
        row = await self._require_revision(meme_id, expected_revision)
        source = _source(row)
        source["use_count"] = int(source.get("use_count", 0)) + 1
        source["last_used_at_ms"] = now_ms
        await self._update_source(
            row,
            source,
            action="edited",
            payload={"use_count": source["use_count"]},
            now_ms=now_ms,
        )
        return _to_meme(row)

    async def list_archive_candidates(
        self,
        namespace_id: str,
        *,
        inactive_before_ms: int,
    ) -> tuple[ModeMeme, ...]:
        rows = list(
            await self._session.scalars(
                select(ModeMemeRow)
                .where(
                    ModeMemeRow.mode_namespace == namespace_id,
                    ModeMemeRow.state == ModeMemeState.ACTIVE.value,
                    ModeMemeRow.updated_at_ms <= inactive_before_ms,
                )
                .order_by(ModeMemeRow.updated_at_ms, ModeMemeRow.meme_id)
            )
        )
        return tuple(_to_meme(row) for row in rows)

    async def _require_revision(
        self,
        meme_id: str,
        expected_revision: int,
    ) -> ModeMemeRow:
        row = await self._session.get(ModeMemeRow, meme_id)
        if row is None or row.revision != expected_revision:
            raise RuntimePersistenceConflictError("mode meme revision is stale")
        return row

    async def _get_meme(self, meme_id: str) -> ModeMeme:
        row = await self._session.get(ModeMemeRow, meme_id)
        if row is None:
            raise RuntimePersistenceInvariantError("mode meme is missing")
        await self._session.refresh(row)
        return _to_meme(row)

    async def _update_source(
        self,
        row: ModeMemeRow,
        source: dict[str, object],
        *,
        action: str,
        payload: dict[str, object],
        now_ms: int,
    ) -> None:
        previous_revision = row.revision
        next_revision = previous_revision + 1
        result = await self._session.execute(
            update(ModeMemeRow)
            .where(
                ModeMemeRow.meme_id == row.meme_id,
                ModeMemeRow.revision == previous_revision,
            )
            .values(
                source_json=canonical_json(source),
                revision=next_revision,
                updated_at_ms=now_ms,
            )
        )
        if result.rowcount != 1:
            raise RuntimePersistenceConflictError("mode meme revision is stale")
        self._session.add(
            ModeMemeEventRow(
                event_id=_meme_event_id(row.meme_id, action, next_revision),
                meme_id=row.meme_id,
                action=action,
                payload_json=canonical_json(payload),
                previous_revision=previous_revision,
                new_revision=next_revision,
                created_at_ms=now_ms,
            )
        )
        await self._session.flush()
        await self._session.refresh(row)

    @staticmethod
    def _validate_candidate(
        row: ModeMemeCandidateRow,
        candidate: MemeCandidate,
    ) -> None:
        if (
            row.room_id != candidate.room_id
            or row.session_id != candidate.session_id
            or row.audience_epoch != candidate.audience_epoch
            or row.observation_id != candidate.observation_id
            or row.mode_namespace != candidate.namespace_id
            or row.idempotency_key != (candidate.idempotency_key or candidate.candidate_id)
            or row.text != candidate.text
            or json.loads(row.evidence_event_ids_json) != candidate.evidence_event_ids
            or json.loads(row.evidence_frame_indexes_json)
            != candidate.evidence_frame_indexes
        ):
            raise RuntimePersistenceConflictError(
                "meme candidate id was used with different content"
            )

    async def _validate_idempotency(self, candidate: MemeCandidate) -> None:
        idempotency_key = candidate.idempotency_key or candidate.candidate_id
        row = await self._session.scalar(
            select(ModeMemeCandidateRow).where(
                ModeMemeCandidateRow.mode_namespace == candidate.namespace_id,
                ModeMemeCandidateRow.idempotency_key == idempotency_key,
            )
        )
        if row is not None and row.candidate_id != candidate.candidate_id:
            raise RuntimePersistenceConflictError(
                "meme idempotency key was used by another candidate"
            )


def _to_memory(
    row: RoomLongTermMemoryRow,
    evidence_event_ids: Sequence[str],
) -> RoomLongTermMemory:
    try:
        memory_type = RoomMemoryType(row.memory_type)
    except ValueError as error:
        raise RuntimePersistenceInvariantError("room memory type is invalid") from error
    return RoomLongTermMemory(
        memory_id=row.memory_id,
        room_id=row.room_id,
        memory_type=memory_type,
        content=row.content,
        evidence_event_ids=list(evidence_event_ids),
        confidence=row.confidence,
        revision=row.revision,
        created_at_ms=row.created_at_ms,
        updated_at_ms=row.updated_at_ms,
        revoked_at_ms=row.updated_at_ms if row.state == "revoked" else None,
    )


def _source(row: ModeMemeRow) -> dict[str, object]:
    try:
        value = json.loads(row.source_json)
    except json.JSONDecodeError as error:
        raise RuntimePersistenceInvariantError("mode meme source is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimePersistenceInvariantError("mode meme source must be an object")
    return value


def _candidate_row(
    candidate: MemeCandidate,
    *,
    outcome: str,
    result_meme_id: str | None = None,
) -> ModeMemeCandidateRow:
    return ModeMemeCandidateRow(
        candidate_id=candidate.candidate_id,
        room_id=candidate.room_id,
        session_id=candidate.session_id,
        audience_epoch=candidate.audience_epoch,
        observation_id=candidate.observation_id,
        mode_namespace=candidate.namespace_id,
        idempotency_key=candidate.idempotency_key or candidate.candidate_id,
        text=candidate.text,
        evidence_event_ids_json=canonical_json(candidate.evidence_event_ids),
        evidence_frame_indexes_json=canonical_json(candidate.evidence_frame_indexes),
        outcome=outcome,
        result_meme_id=result_meme_id,
        created_at_ms=candidate.created_at_ms,
        updated_at_ms=candidate.created_at_ms,
    )


def _to_candidate(row: ModeMemeCandidateRow) -> MemeCandidate:
    return MemeCandidate(
        candidate_id=row.candidate_id,
        room_id=row.room_id,
        session_id=row.session_id,
        audience_epoch=row.audience_epoch,
        observation_id=row.observation_id,
        namespace_id=row.mode_namespace,
        idempotency_key=(
            None if row.idempotency_key == row.candidate_id else row.idempotency_key
        ),
        text=row.text,
        evidence_event_ids=json.loads(row.evidence_event_ids_json),
        evidence_frame_indexes=json.loads(row.evidence_frame_indexes_json),
        outcome=MemeCandidateOutcome(row.outcome),
        created_at_ms=row.created_at_ms,
    )


def _to_auto_ingest_setting(row: ModeMemeSettingRow) -> MemeAutoIngestSetting:
    return MemeAutoIngestSetting(
        namespace_id=row.mode_namespace,
        enabled=row.auto_ingest_enabled,
        revision=row.revision,
    )


def _to_meme(row: ModeMemeRow) -> ModeMeme:
    source = _source(row)
    room_id = source.get("room_id")
    candidate_id = source.get("source_candidate_id")
    if not isinstance(room_id, str) or not isinstance(candidate_id, str):
        raise RuntimePersistenceInvariantError("mode meme source ownership is missing")
    return ModeMeme(
        meme_id=row.meme_id,
        room_id=room_id,
        namespace_id=row.mode_namespace,
        text=row.content,
        intensity=row.intensity,
        source_candidate_id=candidate_id,
        state=ModeMemeState(row.state),
        pinned=bool(source.get("pinned", False)),
        use_count=int(source.get("use_count", 0)),
        revision=row.revision,
        created_at_ms=row.created_at_ms,
        updated_at_ms=row.updated_at_ms,
    )


def _meme_event_id(meme_id: str, action: str, revision: int) -> str:
    return f"meme-event:{action}:{revision}:{meme_id}"
