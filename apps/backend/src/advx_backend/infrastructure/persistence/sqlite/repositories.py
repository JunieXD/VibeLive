from collections.abc import Sequence

from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from advx_backend.application.ports.persistence import (
    EntityNotFoundError,
    PersistenceInvariantError,
    RevisionConflictError,
)
from advx_backend.domain.audience import (
    AudienceMemory,
    AudienceOrigin,
    AudienceProfile,
    HostRelationship,
    MemoryEvidence,
    MemoryOrigin,
    MemoryState,
    PeerRelationship,
    RelationshipUpdatedBy,
)
from advx_backend.domain.session import (
    SessionAudience,
    SessionOutcome,
    SessionRecord,
)
from advx_backend.infrastructure.persistence.sqlite.json_codec import (
    decode_object,
    decode_tags,
    encode_object,
    encode_tags,
)
from advx_backend.infrastructure.persistence.sqlite.models import (
    AudienceHostRelationshipRow,
    AudienceMemoryRow,
    AudiencePeerRelationshipRow,
    AudienceProfileRow,
    MemoryEvidenceRow,
    SessionAudienceRow,
    SessionRecordRow,
)


class SQLiteAudienceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, audience_id: str) -> AudienceProfile | None:
        row = await self._session.get(AudienceProfileRow, audience_id)
        return None if row is None else _to_profile(row)

    async def list_enabled(self) -> list[AudienceProfile]:
        rows = await self._session.scalars(
            select(AudienceProfileRow)
            .where(AudienceProfileRow.enabled == 1)
            .order_by(AudienceProfileRow.updated_at_ms.desc(), AudienceProfileRow.audience_id)
        )
        return [_to_profile(row) for row in rows]

    async def add(self, profile: AudienceProfile) -> None:
        self._session.add(
            AudienceProfileRow(
                audience_id=profile.audience_id,
                display_name=profile.display_name,
                avatar_ref=profile.avatar_ref,
                personality_json=encode_object(profile.personality),
                preferences_json=encode_object(profile.preferences),
                speaking_style_json=encode_object(profile.speaking_style),
                enabled=int(profile.enabled),
                origin=profile.origin.value,
                preset_id=profile.preset_id,
                preset_version=profile.preset_version,
                revision=profile.revision,
                created_at_ms=profile.created_at_ms,
                updated_at_ms=profile.updated_at_ms,
            )
        )
        await self._session.flush()

    async def update(
        self,
        profile: AudienceProfile,
        *,
        expected_revision: int,
    ) -> AudienceProfile:
        new_revision = expected_revision + 1
        result = await self._session.execute(
            update(AudienceProfileRow)
            .where(
                AudienceProfileRow.audience_id == profile.audience_id,
                AudienceProfileRow.revision == expected_revision,
            )
            .values(
                display_name=profile.display_name,
                avatar_ref=profile.avatar_ref,
                personality_json=encode_object(profile.personality),
                preferences_json=encode_object(profile.preferences),
                speaking_style_json=encode_object(profile.speaking_style),
                enabled=int(profile.enabled),
                origin=profile.origin.value,
                preset_id=profile.preset_id,
                preset_version=profile.preset_version,
                revision=new_revision,
                updated_at_ms=profile.updated_at_ms,
            )
        )
        if result.rowcount != 1:
            await _raise_revision_or_missing(
                self._session,
                AudienceProfileRow.audience_id,
                profile.audience_id,
                entity="audience profile",
                expected_revision=expected_revision,
            )
        return profile.model_copy(update={"revision": new_revision})

    async def delete(self, audience_id: str) -> bool:
        result = await self._session.execute(
            delete(AudienceProfileRow).where(AudienceProfileRow.audience_id == audience_id)
        )
        await self._session.flush()
        return result.rowcount == 1


class SQLiteRelationshipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_host(self, audience_id: str) -> HostRelationship | None:
        row = await self._session.get(AudienceHostRelationshipRow, audience_id)
        return None if row is None else _to_host_relationship(row)

    async def save_host(
        self,
        relationship: HostRelationship,
        *,
        expected_revision: int | None,
    ) -> HostRelationship:
        await self._validate_source(
            relationship.audience_id,
            relationship.source_memory_id,
            relationship.updated_by,
        )
        if expected_revision is None:
            created = relationship.model_copy(update={"revision": 1})
            self._session.add(
                AudienceHostRelationshipRow(
                    audience_id=created.audience_id,
                    summary=created.summary,
                    state_json=encode_object(created.state),
                    source_memory_id=created.source_memory_id,
                    updated_by=created.updated_by.value,
                    revision=created.revision,
                    updated_at_ms=created.updated_at_ms,
                )
            )
            await self._session.flush()
            return created

        new_revision = expected_revision + 1
        result = await self._session.execute(
            update(AudienceHostRelationshipRow)
            .where(
                AudienceHostRelationshipRow.audience_id == relationship.audience_id,
                AudienceHostRelationshipRow.revision == expected_revision,
            )
            .values(
                summary=relationship.summary,
                state_json=encode_object(relationship.state),
                source_memory_id=relationship.source_memory_id,
                updated_by=relationship.updated_by.value,
                revision=new_revision,
                updated_at_ms=relationship.updated_at_ms,
            )
        )
        if result.rowcount != 1:
            await _raise_revision_or_missing(
                self._session,
                AudienceHostRelationshipRow.audience_id,
                relationship.audience_id,
                entity="host relationship",
                expected_revision=expected_revision,
            )
        return relationship.model_copy(update={"revision": new_revision})

    async def get_peer(
        self,
        audience_id: str,
        peer_audience_id: str,
    ) -> PeerRelationship | None:
        row = await self._session.get(
            AudiencePeerRelationshipRow,
            (audience_id, peer_audience_id),
        )
        return None if row is None else _to_peer_relationship(row)

    async def list_peers(self, audience_id: str) -> list[PeerRelationship]:
        rows = await self._session.scalars(
            select(AudiencePeerRelationshipRow)
            .where(AudiencePeerRelationshipRow.audience_id == audience_id)
            .order_by(AudiencePeerRelationshipRow.peer_audience_id)
        )
        return [_to_peer_relationship(row) for row in rows]

    async def save_peer(
        self,
        relationship: PeerRelationship,
        *,
        expected_revision: int | None,
    ) -> PeerRelationship:
        await self._validate_source(
            relationship.audience_id,
            relationship.source_memory_id,
            relationship.updated_by,
        )
        if expected_revision is None:
            created = relationship.model_copy(update={"revision": 1})
            self._session.add(
                AudiencePeerRelationshipRow(
                    audience_id=created.audience_id,
                    peer_audience_id=created.peer_audience_id,
                    summary=created.summary,
                    state_json=encode_object(created.state),
                    source_memory_id=created.source_memory_id,
                    updated_by=created.updated_by.value,
                    revision=created.revision,
                    updated_at_ms=created.updated_at_ms,
                )
            )
            await self._session.flush()
            return created

        relationship_id = f"{relationship.audience_id}:{relationship.peer_audience_id}"
        new_revision = expected_revision + 1
        result = await self._session.execute(
            update(AudiencePeerRelationshipRow)
            .where(
                AudiencePeerRelationshipRow.audience_id == relationship.audience_id,
                AudiencePeerRelationshipRow.peer_audience_id == relationship.peer_audience_id,
                AudiencePeerRelationshipRow.revision == expected_revision,
            )
            .values(
                summary=relationship.summary,
                state_json=encode_object(relationship.state),
                source_memory_id=relationship.source_memory_id,
                updated_by=relationship.updated_by.value,
                revision=new_revision,
                updated_at_ms=relationship.updated_at_ms,
            )
        )
        if result.rowcount != 1:
            exists = await self.get_peer(
                relationship.audience_id,
                relationship.peer_audience_id,
            )
            if exists is None:
                raise EntityNotFoundError("peer relationship", relationship_id)
            raise RevisionConflictError(
                "peer relationship",
                relationship_id,
                expected_revision,
            )
        return relationship.model_copy(update={"revision": new_revision})

    async def delete_for_source_memory(self, memory_id: str) -> int:
        host_result = await self._session.execute(
            delete(AudienceHostRelationshipRow).where(
                AudienceHostRelationshipRow.source_memory_id == memory_id
            )
        )
        peer_result = await self._session.execute(
            delete(AudiencePeerRelationshipRow).where(
                AudiencePeerRelationshipRow.source_memory_id == memory_id
            )
        )
        return host_result.rowcount + peer_result.rowcount

    async def _validate_source(
        self,
        audience_id: str,
        source_memory_id: str | None,
        updated_by: RelationshipUpdatedBy,
    ) -> None:
        if updated_by is RelationshipUpdatedBy.MEMORY and source_memory_id is None:
            raise PersistenceInvariantError("memory-derived relationships require source memory")
        if source_memory_id is None:
            return
        source = await self._session.scalar(
            select(AudienceMemoryRow.memory_id)
            .join(
                MemoryEvidenceRow,
                MemoryEvidenceRow.memory_id == AudienceMemoryRow.memory_id,
            )
            .where(
                AudienceMemoryRow.memory_id == source_memory_id,
                AudienceMemoryRow.audience_id == audience_id,
            )
            .limit(1)
        )
        if source is None:
            raise PersistenceInvariantError(
                "relationship source must be owned by the audience and have evidence"
            )


class SQLiteMemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, audience_id: str, memory_id: str) -> AudienceMemory | None:
        row = await self._session.scalar(
            select(AudienceMemoryRow).where(
                AudienceMemoryRow.audience_id == audience_id,
                AudienceMemoryRow.memory_id == memory_id,
            )
        )
        return None if row is None else _to_memory(row)

    async def list_active(
        self,
        audience_id: str,
        *,
        now_ms: int,
        limit: int = 100,
    ) -> list[AudienceMemory]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = await self._session.scalars(
            select(AudienceMemoryRow)
            .where(
                AudienceMemoryRow.audience_id == audience_id,
                AudienceMemoryRow.state == MemoryState.ACTIVE.value,
                or_(
                    AudienceMemoryRow.expires_at_ms.is_(None),
                    AudienceMemoryRow.expires_at_ms > now_ms,
                ),
            )
            .order_by(
                AudienceMemoryRow.importance.desc(),
                AudienceMemoryRow.updated_at_ms.desc(),
                AudienceMemoryRow.memory_id,
            )
            .limit(limit)
        )
        return [_to_memory(row) for row in rows]

    async def add(
        self,
        memory: AudienceMemory,
        evidence: Sequence[MemoryEvidence] = (),
    ) -> None:
        if memory.origin is MemoryOrigin.EXTRACTED and not evidence:
            raise PersistenceInvariantError("extracted memories require evidence")
        if any(item.memory_id != memory.memory_id for item in evidence):
            raise PersistenceInvariantError("evidence must reference the new memory")
        self._session.add(
            AudienceMemoryRow(
                memory_id=memory.memory_id,
                audience_id=memory.audience_id,
                memory_type=memory.memory_type,
                content=memory.content,
                tags_json=encode_tags(memory.tags),
                importance=memory.importance,
                confidence=memory.confidence,
                origin=memory.origin.value,
                state=memory.state.value,
                superseded_by=memory.superseded_by,
                last_recalled_at_ms=memory.last_recalled_at_ms,
                expires_at_ms=memory.expires_at_ms,
                revision=memory.revision,
                created_at_ms=memory.created_at_ms,
                updated_at_ms=memory.updated_at_ms,
            )
        )
        self._session.add_all(
            [
                MemoryEvidenceRow(
                    memory_id=item.memory_id,
                    session_id=item.session_id,
                    source_event_id=item.source_event_id,
                    source_type=item.source_type,
                    occurred_at_ms=item.occurred_at_ms,
                    evidence_summary=item.evidence_summary,
                )
                for item in evidence
            ]
        )
        await self._session.flush()

    async def update(
        self,
        memory: AudienceMemory,
        *,
        expected_revision: int,
    ) -> AudienceMemory:
        if memory.superseded_by is not None:
            await self._validate_replacement(
                memory.audience_id,
                memory.memory_id,
                memory.superseded_by,
            )
        new_revision = expected_revision + 1
        result = await self._session.execute(
            update(AudienceMemoryRow)
            .where(
                AudienceMemoryRow.memory_id == memory.memory_id,
                AudienceMemoryRow.audience_id == memory.audience_id,
                AudienceMemoryRow.revision == expected_revision,
            )
            .values(
                memory_type=memory.memory_type,
                content=memory.content,
                tags_json=encode_tags(memory.tags),
                importance=memory.importance,
                confidence=memory.confidence,
                origin=memory.origin.value,
                state=memory.state.value,
                superseded_by=memory.superseded_by,
                last_recalled_at_ms=memory.last_recalled_at_ms,
                expires_at_ms=memory.expires_at_ms,
                revision=new_revision,
                updated_at_ms=memory.updated_at_ms,
            )
        )
        if result.rowcount != 1:
            await self._raise_memory_revision_or_missing(
                memory.audience_id,
                memory.memory_id,
                expected_revision,
            )
        await self._delete_source_relationships(memory.memory_id)
        return memory.model_copy(update={"revision": new_revision})

    async def supersede(
        self,
        audience_id: str,
        memory_id: str,
        *,
        replacement_id: str,
        expected_revision: int,
        updated_at_ms: int,
    ) -> AudienceMemory:
        await self._validate_replacement(audience_id, memory_id, replacement_id)
        result = await self._session.execute(
            update(AudienceMemoryRow)
            .where(
                AudienceMemoryRow.memory_id == memory_id,
                AudienceMemoryRow.audience_id == audience_id,
                AudienceMemoryRow.revision == expected_revision,
            )
            .values(
                state=MemoryState.SUPERSEDED.value,
                superseded_by=replacement_id,
                revision=expected_revision + 1,
                updated_at_ms=updated_at_ms,
            )
        )
        if result.rowcount != 1:
            await self._raise_memory_revision_or_missing(
                audience_id,
                memory_id,
                expected_revision,
            )
        await self._delete_source_relationships(memory_id)
        updated = await self.get(audience_id, memory_id)
        if updated is None:
            raise EntityNotFoundError("audience memory", memory_id)
        return updated

    async def evidence_for(
        self,
        audience_id: str,
        memory_id: str,
    ) -> list[MemoryEvidence]:
        rows = await self._session.scalars(
            select(MemoryEvidenceRow)
            .join(
                AudienceMemoryRow,
                AudienceMemoryRow.memory_id == MemoryEvidenceRow.memory_id,
            )
            .where(MemoryEvidenceRow.memory_id == memory_id)
            .where(AudienceMemoryRow.audience_id == audience_id)
            .order_by(
                MemoryEvidenceRow.occurred_at_ms,
                MemoryEvidenceRow.session_id,
                MemoryEvidenceRow.source_event_id,
            )
        )
        return [_to_evidence(row) for row in rows]

    async def delete(self, audience_id: str, memory_id: str) -> bool:
        memory = await self.get(audience_id, memory_id)
        if memory is None:
            return False
        await self._delete_source_relationships(memory_id)
        result = await self._session.execute(
            delete(AudienceMemoryRow).where(
                AudienceMemoryRow.audience_id == audience_id,
                AudienceMemoryRow.memory_id == memory_id,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def _validate_replacement(
        self,
        audience_id: str,
        memory_id: str,
        replacement_id: str,
    ) -> None:
        if memory_id == replacement_id:
            raise PersistenceInvariantError("a memory cannot supersede itself")
        replacement = await self.get(audience_id, replacement_id)
        if replacement is None or replacement.state is not MemoryState.ACTIVE:
            raise PersistenceInvariantError(
                "replacement memory must be active and owned by the same audience"
            )

    async def _delete_source_relationships(self, memory_id: str) -> None:
        await self._session.execute(
            delete(AudienceHostRelationshipRow).where(
                AudienceHostRelationshipRow.source_memory_id == memory_id
            )
        )
        await self._session.execute(
            delete(AudiencePeerRelationshipRow).where(
                AudiencePeerRelationshipRow.source_memory_id == memory_id
            )
        )

    async def _raise_memory_revision_or_missing(
        self,
        audience_id: str,
        memory_id: str,
        expected_revision: int,
    ) -> None:
        exists = await self.get(audience_id, memory_id)
        if exists is None:
            raise EntityNotFoundError("audience memory", memory_id)
        raise RevisionConflictError("audience memory", memory_id, expected_revision)


class SQLiteSessionRecordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: str) -> SessionRecord | None:
        row = await self._session.get(SessionRecordRow, session_id)
        return None if row is None else _to_session_record(row)

    async def add(self, record: SessionRecord) -> None:
        self._session.add(
            SessionRecordRow(
                session_id=record.session_id,
                started_at_ms=record.started_at_ms,
                ended_at_ms=record.ended_at_ms,
                outcome=None if record.outcome is None else record.outcome.value,
                app_version=record.app_version,
            )
        )
        await self._session.flush()

    async def add_audience(self, audience: SessionAudience) -> None:
        self._session.add(
            SessionAudienceRow(
                session_id=audience.session_id,
                audience_id=audience.audience_id,
                profile_revision=audience.profile_revision,
                joined_at_ms=audience.joined_at_ms,
                left_at_ms=audience.left_at_ms,
            )
        )
        await self._session.flush()

    async def list_audiences(self, session_id: str) -> list[SessionAudience]:
        rows = await self._session.scalars(
            select(SessionAudienceRow)
            .where(SessionAudienceRow.session_id == session_id)
            .order_by(SessionAudienceRow.audience_id)
        )
        return [
            SessionAudience(
                session_id=row.session_id,
                audience_id=row.audience_id,
                profile_revision=row.profile_revision,
                joined_at_ms=row.joined_at_ms,
                left_at_ms=row.left_at_ms,
            )
            for row in rows
        ]

    async def finish(
        self,
        session_id: str,
        *,
        ended_at_ms: int,
        outcome: SessionOutcome,
    ) -> SessionRecord:
        result = await self._session.execute(
            update(SessionRecordRow)
            .where(
                SessionRecordRow.session_id == session_id,
                SessionRecordRow.ended_at_ms.is_(None),
            )
            .values(
                state="stopped",
                ended_at_ms=ended_at_ms,
                outcome=outcome.value,
            )
        )
        if result.rowcount != 1:
            existing = await self.get(session_id)
            if existing is None:
                raise EntityNotFoundError("session record", session_id)
            raise PersistenceInvariantError(f"session record {session_id} is already finished")
        finished = await self.get(session_id)
        if finished is None:
            raise EntityNotFoundError("session record", session_id)
        return finished

    async def recover_interrupted(self, *, ended_at_ms: int) -> int:
        result = await self._session.execute(
            update(SessionRecordRow)
            .where(SessionRecordRow.ended_at_ms.is_(None))
            .values(
                state="stopped",
                ended_at_ms=case(
                    (
                        SessionRecordRow.started_at_ms > ended_at_ms,
                        SessionRecordRow.started_at_ms,
                    ),
                    else_=ended_at_ms,
                ),
                outcome=SessionOutcome.INTERRUPTED.value,
            )
        )
        return result.rowcount


async def _raise_revision_or_missing(
    session: AsyncSession,
    id_column: object,
    entity_id: str,
    *,
    entity: str,
    expected_revision: int,
) -> None:
    exists = await session.scalar(select(id_column).where(id_column == entity_id))
    if exists is None:
        raise EntityNotFoundError(entity, entity_id)
    raise RevisionConflictError(entity, entity_id, expected_revision)


def _to_profile(row: AudienceProfileRow) -> AudienceProfile:
    return AudienceProfile(
        audience_id=row.audience_id,
        display_name=row.display_name,
        avatar_ref=row.avatar_ref,
        personality=decode_object(row.personality_json),
        preferences=decode_object(row.preferences_json),
        speaking_style=decode_object(row.speaking_style_json),
        enabled=bool(row.enabled),
        origin=AudienceOrigin(row.origin),
        preset_id=row.preset_id,
        preset_version=row.preset_version,
        revision=row.revision,
        created_at_ms=row.created_at_ms,
        updated_at_ms=row.updated_at_ms,
    )


def _to_memory(row: AudienceMemoryRow) -> AudienceMemory:
    return AudienceMemory(
        memory_id=row.memory_id,
        audience_id=row.audience_id,
        memory_type=row.memory_type,
        content=row.content,
        tags=decode_tags(row.tags_json),
        importance=row.importance,
        confidence=row.confidence,
        origin=MemoryOrigin(row.origin),
        state=MemoryState(row.state),
        superseded_by=row.superseded_by,
        last_recalled_at_ms=row.last_recalled_at_ms,
        expires_at_ms=row.expires_at_ms,
        revision=row.revision,
        created_at_ms=row.created_at_ms,
        updated_at_ms=row.updated_at_ms,
    )


def _to_evidence(row: MemoryEvidenceRow) -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=row.memory_id,
        session_id=row.session_id,
        source_event_id=row.source_event_id,
        source_type=row.source_type,
        occurred_at_ms=row.occurred_at_ms,
        evidence_summary=row.evidence_summary,
    )


def _to_host_relationship(row: AudienceHostRelationshipRow) -> HostRelationship:
    return HostRelationship(
        audience_id=row.audience_id,
        summary=row.summary,
        state=decode_object(row.state_json),
        source_memory_id=row.source_memory_id,
        updated_by=RelationshipUpdatedBy(row.updated_by),
        revision=row.revision,
        updated_at_ms=row.updated_at_ms,
    )


def _to_peer_relationship(row: AudiencePeerRelationshipRow) -> PeerRelationship:
    return PeerRelationship(
        audience_id=row.audience_id,
        peer_audience_id=row.peer_audience_id,
        summary=row.summary,
        state=decode_object(row.state_json),
        source_memory_id=row.source_memory_id,
        updated_by=RelationshipUpdatedBy(row.updated_by),
        revision=row.revision,
        updated_at_ms=row.updated_at_ms,
    )


def _to_session_record(row: SessionRecordRow) -> SessionRecord:
    return SessionRecord(
        session_id=row.session_id,
        started_at_ms=row.started_at_ms,
        ended_at_ms=row.ended_at_ms,
        outcome=None if row.outcome is None else SessionOutcome(row.outcome),
        app_version=row.app_version,
    )
