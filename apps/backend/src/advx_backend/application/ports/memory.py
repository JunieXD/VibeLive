from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from advx_backend.domain.memory import RoomLongTermMemory, RoomMemorySlice, RoomMemoryType


@dataclass(frozen=True)
class RoomMemoryCandidate:
    candidate_id: str
    room_id: str
    session_id: str
    audience_epoch: int
    idempotency_key: str
    base_revision: int
    memory_id: str
    memory_type: RoomMemoryType
    content: str
    evidence_event_ids: tuple[str, ...]
    tags: tuple[str, ...] = ()
    origin: str = "extracted"
    importance: float = 0.5
    confidence: float = 0.5


@dataclass(frozen=True)
class MemoryEvidence:
    event_id: str
    room_id: str
    source_type: str
    occurred_at_ms: int
    summary: str = ""


@dataclass(frozen=True)
class MemoryCommitResult:
    accepted: bool
    memory_id: str | None = None
    memory_revision: int | None = None
    head_revision: int | None = None
    created: bool = False
    reason: str | None = None


class RoomMemoryRepository(Protocol):
    async def head_revision(self, room_id: str) -> int: ...

    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice: ...

    async def commit_candidate(
        self,
        candidate: RoomMemoryCandidate,
        *,
        evidence: Sequence[MemoryEvidence],
        now_ms: int,
    ) -> MemoryCommitResult: ...

    async def list_active(self, room_id: str) -> tuple[RoomLongTermMemory, ...]: ...

    async def get(self, room_id: str, memory_id: str) -> RoomLongTermMemory: ...

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
    ) -> RoomLongTermMemory: ...

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
    ) -> RoomLongTermMemory: ...

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
    ) -> RoomLongTermMemory: ...

    async def revoke(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> RoomLongTermMemory: ...

    async def delete(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> bool: ...

    async def reset(
        self,
        room_id: str,
        *,
        expected_revision: int,
        now_ms: int,
    ) -> int: ...


class RoomEventReader(Protocol):
    async def read_events(
        self,
        event_ids: tuple[str, ...],
    ) -> tuple[MemoryEvidence, ...]: ...


class SessionFence(Protocol):
    async def accepts(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        namespace_id: str | None = None,
    ) -> bool: ...

    async def execute_if_accepting(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        operation: Callable[[], Awaitable[object]],
        namespace_id: str | None = None,
    ) -> tuple[bool, object | None]: ...
