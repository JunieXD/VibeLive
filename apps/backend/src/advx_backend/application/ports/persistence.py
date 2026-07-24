from collections.abc import Sequence
from typing import Protocol, Self

from advx_backend.domain.audience import (
    AudienceMemory,
    AudienceProfile,
    HostRelationship,
    MemoryEvidence,
    PeerRelationship,
)
from advx_backend.domain.session import (
    SessionAudience,
    SessionOutcome,
    SessionRecord,
)


class PersistenceError(RuntimeError):
    pass


class EntityNotFoundError(PersistenceError):
    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} {entity_id} was not found")


class RevisionConflictError(PersistenceError):
    def __init__(self, entity: str, entity_id: str, expected_revision: int) -> None:
        self.entity = entity
        self.entity_id = entity_id
        self.expected_revision = expected_revision
        super().__init__(f"{entity} {entity_id} no longer has revision {expected_revision}")


class PersistenceInvariantError(PersistenceError):
    pass


class AudienceRepository(Protocol):
    async def get(self, audience_id: str) -> AudienceProfile | None: ...

    async def list_enabled(self) -> list[AudienceProfile]: ...

    async def add(self, profile: AudienceProfile) -> None: ...

    async def update(
        self,
        profile: AudienceProfile,
        *,
        expected_revision: int,
    ) -> AudienceProfile: ...

    async def delete(self, audience_id: str) -> bool: ...


class RelationshipRepository(Protocol):
    async def get_host(self, audience_id: str) -> HostRelationship | None: ...

    async def save_host(
        self,
        relationship: HostRelationship,
        *,
        expected_revision: int | None,
    ) -> HostRelationship: ...

    async def get_peer(
        self,
        audience_id: str,
        peer_audience_id: str,
    ) -> PeerRelationship | None: ...

    async def list_peers(self, audience_id: str) -> list[PeerRelationship]: ...

    async def save_peer(
        self,
        relationship: PeerRelationship,
        *,
        expected_revision: int | None,
    ) -> PeerRelationship: ...

    async def delete_for_source_memory(self, memory_id: str) -> int: ...


class MemoryRepository(Protocol):
    async def get(self, audience_id: str, memory_id: str) -> AudienceMemory | None: ...

    async def list_active(
        self,
        audience_id: str,
        *,
        now_ms: int,
        limit: int = 100,
    ) -> list[AudienceMemory]: ...

    async def add(
        self,
        memory: AudienceMemory,
        evidence: Sequence[MemoryEvidence] = (),
    ) -> None: ...

    async def update(
        self,
        memory: AudienceMemory,
        *,
        expected_revision: int,
    ) -> AudienceMemory: ...

    async def supersede(
        self,
        audience_id: str,
        memory_id: str,
        *,
        replacement_id: str,
        expected_revision: int,
        updated_at_ms: int,
    ) -> AudienceMemory: ...

    async def evidence_for(
        self,
        audience_id: str,
        memory_id: str,
    ) -> list[MemoryEvidence]: ...

    async def delete(self, audience_id: str, memory_id: str) -> bool: ...


class SessionRecordRepository(Protocol):
    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def add(self, record: SessionRecord) -> None: ...

    async def add_audience(self, audience: SessionAudience) -> None: ...

    async def list_audiences(self, session_id: str) -> list[SessionAudience]: ...

    async def finish(
        self,
        session_id: str,
        *,
        ended_at_ms: int,
        outcome: SessionOutcome,
    ) -> SessionRecord: ...

    async def recover_interrupted(self, *, ended_at_ms: int) -> int: ...


class UnitOfWork(Protocol):
    audiences: AudienceRepository
    relationships: RelationshipRepository
    memories: MemoryRepository
    sessions: SessionRecordRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class SessionRecordStore(Protocol):
    async def record_started(self, record: SessionRecord) -> None: ...

    async def record_finished(
        self,
        session_id: str,
        *,
        ended_at_ms: int,
        outcome: SessionOutcome,
    ) -> None: ...

    async def recover_interrupted(self, *, ended_at_ms: int) -> int: ...
