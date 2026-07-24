from collections.abc import Sequence

from advx_backend.application.ports.memory import (
    MemoryCommitResult,
    MemoryEvidence,
    RoomEventReader,
    RoomMemoryCandidate,
    RoomMemoryRepository,
    SessionFence,
)
from advx_backend.application.ports.session import Clock
from advx_backend.domain.memory import RoomLongTermMemory, RoomMemorySlice, RoomMemoryType

_NON_AI_EVIDENCE = {
    "user_text",
    "user_voice",
    "final_voice",
    "screen_observation",
    "system",
}


class RoomMemoryService:
    def __init__(
        self,
        *,
        repository: RoomMemoryRepository,
        event_reader: RoomEventReader,
        session_fence: SessionFence,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._event_reader = event_reader
        self._session_fence = session_fence
        self._clock = clock

    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice:
        if limit < 1:
            raise ValueError("memory slice limit must be positive")
        return await self._repository.read_slice(
            room_id=room_id,
            event_ids=event_ids,
            limit=limit,
        )

    async def commit_candidate(
        self,
        candidate: RoomMemoryCandidate,
    ) -> MemoryCommitResult:
        if not await self._session_fence.accepts(
            room_id=candidate.room_id,
            session_id=candidate.session_id,
            audience_epoch=candidate.audience_epoch,
        ):
            return MemoryCommitResult(accepted=False, reason="stale_epoch")

        evidence = await self._event_reader.read_events(candidate.evidence_event_ids)
        evidence_error = self._validate_evidence(candidate, evidence)
        if evidence_error is not None:
            return MemoryCommitResult(accepted=False, reason=evidence_error)

        return await self._repository.commit_candidate(
            candidate,
            evidence=evidence,
            now_ms=self._clock.now_ms(),
        )

    async def list_active(self, room_id: str) -> tuple[RoomLongTermMemory, ...]:
        return await self._repository.list_active(room_id)

    async def get(self, room_id: str, memory_id: str) -> RoomLongTermMemory:
        return await self._repository.get(room_id, memory_id)

    async def edit(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        content: str,
        confidence: float,
        evidence_event_ids: tuple[str, ...],
    ) -> RoomLongTermMemory:
        return await self._repository.edit(
            room_id,
            memory_id,
            expected_revision=expected_revision,
            content=content,
            confidence=confidence,
            evidence_event_ids=evidence_event_ids,
            now_ms=self._clock.now_ms(),
        )

    async def merge(
        self,
        room_id: str,
        memory_id: str,
        source_memory_id: str,
        *,
        expected_revision: int,
        source_expected_revision: int,
        content: str,
    ) -> RoomLongTermMemory:
        return await self._repository.merge(
            room_id,
            memory_id,
            source_memory_id,
            expected_revision=expected_revision,
            source_expected_revision=source_expected_revision,
            content=content,
            now_ms=self._clock.now_ms(),
        )

    async def replace(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        replacement_memory_id: str,
        content: str,
        evidence_event_ids: tuple[str, ...],
    ) -> RoomLongTermMemory:
        return await self._repository.replace(
            room_id,
            memory_id,
            expected_revision=expected_revision,
            replacement_memory_id=replacement_memory_id,
            content=content,
            evidence_event_ids=evidence_event_ids,
            now_ms=self._clock.now_ms(),
        )

    async def revoke(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
    ) -> RoomLongTermMemory:
        return await self._repository.revoke(
            room_id,
            memory_id,
            expected_revision=expected_revision,
            now_ms=self._clock.now_ms(),
        )

    async def delete(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
    ) -> bool:
        return await self._repository.delete(
            room_id,
            memory_id,
            expected_revision=expected_revision,
            now_ms=self._clock.now_ms(),
        )

    async def reset(self, room_id: str, *, expected_revision: int) -> int:
        return await self._repository.reset(
            room_id,
            expected_revision=expected_revision,
            now_ms=self._clock.now_ms(),
        )

    @staticmethod
    def _validate_evidence(
        candidate: RoomMemoryCandidate,
        evidence: Sequence[MemoryEvidence],
    ) -> str | None:
        requested_ids = set(candidate.evidence_event_ids)
        if not requested_ids or {item.event_id for item in evidence} != requested_ids:
            return "missing_evidence"
        if any(item.room_id != candidate.room_id for item in evidence):
            return "cross_room_evidence"
        if (
            candidate.memory_type is not RoomMemoryType.ROOM_LORE
            and not any(item.source_type in _NON_AI_EVIDENCE for item in evidence)
        ):
            return "non_ai_evidence_required"
        return None
