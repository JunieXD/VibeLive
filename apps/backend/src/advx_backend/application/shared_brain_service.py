import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from advx_backend.application.memory_service import RoomMemoryService
from advx_backend.application.ports.meme import (
    MemeAutoIngestSetting,
    MemeCommitResult,
)
from advx_backend.application.ports.memory import (
    MemoryCommitResult,
    RoomMemoryCandidate,
    SessionFence,
)
from advx_backend.application.ports.session import Clock
from advx_backend.application.room_event_persistence import (
    PersistentRuntimeRoomEventStore,
)
from advx_backend.application.room_service import RoomService
from advx_backend.domain.meme import MemeCandidate, ModeMeme, ModeMemeState
from advx_backend.domain.memory import RoomLongTermMemory, RoomMemorySlice
from advx_backend.domain.room import RoomEvent, RoomEventSource
from advx_backend.infrastructure.persistence.sqlite.memory_meme_adapters import (
    SQLiteModeMemeServiceRepository,
    SQLiteRoomEventReader,
    SQLiteRoomMemoryServiceRepository,
)
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    RuntimePersistenceConflictError,
    RuntimePersistenceInvariantError,
)


class ObservationWaveProvenance(Protocol):
    room_id: str
    session_id: str
    audience_epoch: int
    observation_id: str
    event_ids: list[str]
    trigger_event_ids: list[str]
    frame_hashes: list[str]


class ObservationWaveProvenanceReader(Protocol):
    def observation_wave(
        self,
        observation_id: str,
    ) -> ObservationWaveProvenance | None: ...


@dataclass(frozen=True, slots=True)
class LegacyMemeImportResult:
    candidate_id: str
    meme_id: str
    provenance_event_id: str
    created: bool


class _RoomLockRegistry:
    """A fixed set of locks that serializes every room without unbounded growth."""

    def __init__(self, size: int = 64) -> None:
        self._locks = tuple(asyncio.Lock() for _ in range(size))

    def lock_for(self, room_id: str) -> asyncio.Lock:
        digest = hashlib.sha256(room_id.encode("utf-8")).digest()
        return self._locks[int.from_bytes(digest[:8], "big") % len(self._locks)]


class _LoopOwnedRoomLockRegistry:
    """Own room locks on one active event loop and replace them after shutdown."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._locks: _RoomLockRegistry | None = None

    def for_running_loop(self) -> _RoomLockRegistry:
        loop = asyncio.get_running_loop()
        with self._guard:
            if self._loop is not None and self._loop is not loop:
                if not self._loop.is_closed():
                    raise RuntimeError(
                        "a memory session factory cannot be shared across active "
                        "event loops"
                    )
                self._loop = None
                self._locks = None
            if self._locks is None:
                self._loop = loop
                self._locks = _RoomLockRegistry()
            return self._locks


_MEMORY_COMMIT_LOCKS_BY_FACTORY: WeakKeyDictionary[
    object, _LoopOwnedRoomLockRegistry
] = (
    WeakKeyDictionary()
)
_MEMORY_COMMIT_LOCKS_GUARD = Lock()


def _memory_commit_locks_for(session_factory: object) -> _LoopOwnedRoomLockRegistry:
    with _MEMORY_COMMIT_LOCKS_GUARD:
        registry = _MEMORY_COMMIT_LOCKS_BY_FACTORY.get(session_factory)
        if registry is None:
            registry = _LoopOwnedRoomLockRegistry()
            _MEMORY_COMMIT_LOCKS_BY_FACTORY[session_factory] = registry
        return registry


class SharedBrainService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        runtime_state: SessionFence,
        clock: Clock,
        room_service: RoomService | None = None,
        room_event_store: PersistentRuntimeRoomEventStore | None = None,
        observation_provenance: ObservationWaveProvenanceReader | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._runtime_state = runtime_state
        self._clock = clock
        self._room_service = room_service
        self._room_event_store = room_event_store
        self._observation_provenance = observation_provenance
        self._memory_commit_lock_owner = _memory_commit_locks_for(session_factory)

    async def list_memories(self, room_id: str) -> tuple[RoomLongTermMemory, ...]:
        async with self._session_factory() as session:
            return await SQLiteRoomMemoryServiceRepository(session).list_active(room_id)

    async def get_memory_head(self, room_id: str) -> int:
        async with self._session_factory() as session:
            return await SQLiteRoomMemoryServiceRepository(session).head_revision(
                room_id
            )

    async def get_memory(
        self,
        room_id: str,
        memory_id: str,
    ) -> RoomLongTermMemory:
        async with self._session_factory() as session:
            return await SQLiteRoomMemoryServiceRepository(session).get(
                room_id,
                memory_id,
            )

    async def edit_memory(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        content: str,
        confidence: float,
        evidence_event_ids: tuple[str, ...] | None,
    ) -> RoomLongTermMemory:
        async with self._session_factory() as session:
            repository = SQLiteRoomMemoryServiceRepository(session)
            current = await repository.get(room_id, memory_id)
            result = await repository.edit(
                room_id,
                memory_id,
                expected_revision=expected_revision,
                content=content,
                confidence=confidence,
                evidence_event_ids=(
                    tuple(current.evidence_event_ids)
                    if evidence_event_ids is None
                    else evidence_event_ids
                ),
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return result

    async def merge_memory(
        self,
        room_id: str,
        memory_id: str,
        source_memory_id: str,
        *,
        expected_revision: int,
        source_expected_revision: int,
        content: str,
    ) -> RoomLongTermMemory:
        async with self._session_factory() as session:
            result = await SQLiteRoomMemoryServiceRepository(session).merge(
                room_id,
                memory_id,
                source_memory_id,
                expected_revision=expected_revision,
                source_expected_revision=source_expected_revision,
                content=content,
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return result

    async def replace_memory(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
        replacement_memory_id: str,
        content: str,
        evidence_event_ids: tuple[str, ...],
    ) -> RoomLongTermMemory:
        async with self._session_factory() as session:
            result = await SQLiteRoomMemoryServiceRepository(session).replace(
                room_id,
                memory_id,
                expected_revision=expected_revision,
                replacement_memory_id=replacement_memory_id,
                content=content,
                evidence_event_ids=evidence_event_ids,
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return result

    async def read_slice(
        self,
        *,
        room_id: str,
        event_ids: tuple[str, ...],
        limit: int,
    ) -> RoomMemorySlice:
        async with self._session_factory() as session:
            return await SQLiteRoomMemoryServiceRepository(session).read_slice(
                room_id=room_id,
                event_ids=event_ids,
                limit=limit,
            )

    async def commit_memory_candidate(
        self,
        candidate: RoomMemoryCandidate,
    ) -> MemoryCommitResult:
        async def commit() -> MemoryCommitResult:
            locks = self._memory_commit_lock_owner.for_running_loop()
            async with locks.lock_for(candidate.room_id):
                for attempt in range(2):
                    async with self._session_factory() as session:
                        service = RoomMemoryService(
                            repository=SQLiteRoomMemoryServiceRepository(session),
                            event_reader=SQLiteRoomEventReader(session),
                            session_fence=_AcceptedFence(),
                            clock=self._clock,
                        )
                        try:
                            result = await service.commit_candidate(candidate)
                            if result.accepted:
                                await session.commit()
                            return result
                        except RuntimePersistenceConflictError as error:
                            await session.rollback()
                            if str(error) != "room memory head is stale":
                                raise
                            if attempt == 1:
                                return MemoryCommitResult(
                                    accepted=False,
                                    reason="stale_head",
                                )
                raise AssertionError("memory commit retry loop did not return")

        accepted, result = await self._execute_if_accepting(candidate, commit)
        if not accepted:
            return MemoryCommitResult(accepted=False, reason="stale_epoch")
        assert isinstance(result, MemoryCommitResult)
        return result

    async def revoke_memory(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
    ) -> RoomLongTermMemory:
        async with self._session_factory() as session:
            repository = SQLiteRoomMemoryServiceRepository(session)
            result = await repository.revoke(
                room_id,
                memory_id,
                expected_revision=expected_revision,
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return result

    async def delete_memory(
        self,
        room_id: str,
        memory_id: str,
        *,
        expected_revision: int,
    ) -> bool:
        async with self._session_factory() as session:
            repository = SQLiteRoomMemoryServiceRepository(session)
            deleted = await repository.delete(
                room_id,
                memory_id,
                expected_revision=expected_revision,
                now_ms=self._clock.now_ms(),
            )
            if deleted:
                await session.commit()
            return deleted

    async def reset_memories(
        self,
        room_id: str,
        *,
        expected_revision: int,
    ) -> int:
        async with self._session_factory() as session:
            repository = SQLiteRoomMemoryServiceRepository(session)
            count = await repository.reset(
                room_id,
                expected_revision=expected_revision,
                now_ms=self._clock.now_ms(),
            )
            if count:
                await session.commit()
            return count

    async def list_active_memes(self, namespace_id: str) -> tuple[ModeMeme, ...]:
        async with self._session_factory() as session:
            return await SQLiteModeMemeServiceRepository(session).list_active(namespace_id)

    async def list_memes(self, namespace_id: str) -> tuple[ModeMeme, ...]:
        async with self._session_factory() as session:
            return await SQLiteModeMemeServiceRepository(session).list_all(namespace_id)

    async def list_pending_candidates(
        self,
        namespace_id: str,
    ) -> tuple[MemeCandidate, ...]:
        async with self._session_factory() as session:
            return await SQLiteModeMemeServiceRepository(session).list_pending(namespace_id)

    async def get_auto_ingest(self, namespace_id: str) -> MemeAutoIngestSetting:
        async with self._session_factory() as session:
            return await SQLiteModeMemeServiceRepository(session).get_auto_ingest(
                namespace_id
            )

    async def set_auto_ingest(
        self,
        namespace_id: str,
        *,
        enabled: bool,
        expected_revision: int,
    ) -> MemeAutoIngestSetting:
        async with self._session_factory() as session:
            setting = await SQLiteModeMemeServiceRepository(session).set_auto_ingest(
                namespace_id,
                enabled=enabled,
                expected_revision=expected_revision,
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return setting

    async def commit_meme_candidate(
        self,
        candidate: MemeCandidate,
    ) -> MemeCommitResult:
        await self._validate_meme_candidate_provenance(candidate)

        async def commit() -> MemeCommitResult:
            async with self._session_factory() as session:
                repository = SQLiteModeMemeServiceRepository(session)
                setting = await repository.get_auto_ingest(candidate.namespace_id)
                if setting.enabled:
                    result = await repository.commit_candidate(candidate)
                else:
                    await repository.save_candidate(candidate)
                    result = MemeCommitResult(
                        accepted=False,
                        pending=True,
                        reason="auto_ingest_disabled",
                    )
                await session.commit()
                return result

        accepted, result = await self._execute_if_accepting(candidate, commit)
        if not accepted:
            return MemeCommitResult(accepted=False, reason="stale_epoch")
        assert isinstance(result, MemeCommitResult)
        return result

    async def import_legacy_meme(
        self,
        namespace_id: str,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        legacy_meme_id: str,
        text: str,
        legacy_created_at_ms: int | None,
    ) -> LegacyMemeImportResult:
        if self._room_service is None or self._room_event_store is None:
            raise RuntimePersistenceInvariantError(
                "legacy meme import provenance is unavailable"
            )
        digest = hashlib.sha256(
            f"{namespace_id}\0{legacy_meme_id}".encode()
        ).hexdigest()
        candidate_id = f"legacy-meme:{digest[:32]}"
        meme_id = f"meme:{candidate_id}"
        provenance_event_id = f"legacy-meme-event:{digest[:32]}"

        async def import_meme() -> LegacyMemeImportResult:
            async with self._session_factory() as session:
                existing = await SQLiteModeMemeServiceRepository(
                    session
                ).find_candidate(candidate_id)
            if existing is not None:
                if (
                    existing.namespace_id != namespace_id
                    or existing.text != text
                    or existing.outcome.value != "accepted"
                    or existing.evidence_event_ids != [provenance_event_id]
                ):
                    raise RuntimePersistenceConflictError(
                        "legacy meme id was used with different content"
                    )
                return LegacyMemeImportResult(
                    candidate_id=candidate_id,
                    meme_id=meme_id,
                    provenance_event_id=provenance_event_id,
                    created=False,
                )

            async def persist(event: RoomEvent) -> None:
                async with self._session_factory() as session:
                    await self._room_event_store.persist_in_session(event, session)
                    candidate = MemeCandidate(
                        candidate_id=candidate_id,
                        room_id=room_id,
                        session_id=session_id,
                        audience_epoch=audience_epoch,
                        observation_id=f"legacy-import:{digest[:32]}",
                        namespace_id=namespace_id,
                        text=text,
                        idempotency_key=candidate_id,
                        evidence_event_ids=[provenance_event_id],
                        created_at_ms=(
                            event.created_at_ms
                            if legacy_created_at_ms is None
                            else legacy_created_at_ms
                        ),
                    )
                    result = await SQLiteModeMemeServiceRepository(
                        session
                    ).commit_candidate(candidate)
                    if not result.accepted or result.meme_id != meme_id:
                        raise RuntimePersistenceInvariantError(
                            "legacy meme import did not commit"
                        )
                    await session.commit()

            await self._room_service.append_event_after(
                session_id,
                event_id=provenance_event_id,
                source_type=RoomEventSource.SYSTEM_EVENT,
                source_id=candidate_id,
                text=text,
                payload={
                    "event": "legacy_meme_import",
                    "reason": "legacy_workspace_migration",
                    "mode_id": namespace_id,
                },
                persist=persist,
            )
            return LegacyMemeImportResult(
                candidate_id=candidate_id,
                meme_id=meme_id,
                provenance_event_id=provenance_event_id,
                created=True,
            )

        accepted, result = await self._runtime_state.execute_if_accepting(
            room_id=room_id,
            session_id=session_id,
            audience_epoch=audience_epoch,
            namespace_id=namespace_id,
            operation=import_meme,
        )
        if not accepted:
            raise RuntimePersistenceInvariantError(
                "legacy meme import runtime scope is stale"
            )
        assert isinstance(result, LegacyMemeImportResult)
        return result

    async def approve_meme_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
    ) -> MemeCommitResult:
        async with self._session_factory() as session:
            candidate = await SQLiteModeMemeServiceRepository(session).get_candidate(
                namespace_id,
                candidate_id,
            )
        await self._validate_meme_candidate_provenance(candidate)

        async def approve() -> MemeCommitResult:
            async with self._session_factory() as session:
                result = await SQLiteModeMemeServiceRepository(
                    session
                ).approve_candidate(
                    namespace_id,
                    candidate_id,
                    now_ms=self._clock.now_ms(),
                )
                await session.commit()
                return result

        accepted, result = await self._execute_if_accepting(candidate, approve)
        if accepted:
            assert isinstance(result, MemeCommitResult)
            return result
        return MemeCommitResult(accepted=False, reason="stale_epoch")

    async def reject_meme_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
    ) -> MemeCandidate:
        async with self._session_factory() as session:
            candidate = await SQLiteModeMemeServiceRepository(session).get_candidate(
                namespace_id,
                candidate_id,
            )

        async def reject() -> MemeCandidate:
            async with self._session_factory() as session:
                result = await SQLiteModeMemeServiceRepository(
                    session
                ).reject_candidate(
                    namespace_id,
                    candidate_id,
                    now_ms=self._clock.now_ms(),
                )
                await session.commit()
                return result

        accepted, result = await self._execute_if_accepting(candidate, reject)
        if not accepted:
            raise RuntimePersistenceInvariantError(
                "meme candidate runtime scope is stale"
            )
        assert isinstance(result, MemeCandidate)
        return result

    async def edit_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
        text: str,
        intensity: float | None,
    ) -> ModeMeme:
        async with self._session_factory() as session:
            repository = SQLiteModeMemeServiceRepository(session)
            await self._assert_meme_namespace(repository, namespace_id, meme_id)
            current = await repository.get(meme_id)
            result = await repository.edit(
                meme_id,
                expected_revision=expected_revision,
                text=text,
                intensity=current.intensity if intensity is None else intensity,
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return result

    async def maintain_memes(self, namespace_id: str) -> tuple[str, ...]:
        now_ms = self._clock.now_ms()
        inactive_before_ms = now_ms - 30 * 24 * 60 * 60 * 1_000
        async with self._session_factory() as session:
            repository = SQLiteModeMemeServiceRepository(session)
            candidates = await repository.list_archive_candidates(
                namespace_id,
                inactive_before_ms=inactive_before_ms,
            )
            archived: list[str] = []
            for meme in candidates:
                if meme.pinned:
                    continue
                decayed = await repository.edit(
                    meme.meme_id,
                    expected_revision=meme.revision,
                    text=meme.text,
                    intensity=max(0.0, meme.intensity * 0.5),
                    now_ms=now_ms,
                )
                if decayed.intensity <= 0.1 or decayed.use_count < 3:
                    await repository.change_state(
                        meme.meme_id,
                        expected_revision=decayed.revision,
                        state=ModeMemeState.ARCHIVED,
                        action="archived",
                        now_ms=now_ms,
                    )
                    archived.append(meme.meme_id)
            await session.commit()
            return tuple(archived)

    async def undo_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        async with self._session_factory() as session:
            repository = SQLiteModeMemeServiceRepository(session)
            await self._assert_meme_namespace(repository, namespace_id, meme_id)
            meme = await repository.get(meme_id)
            candidate = await repository.get_candidate(
                namespace_id,
                meme.source_candidate_id,
            )

        async def undo() -> ModeMeme:
            return await self.revoke_meme(
                namespace_id,
                meme_id,
                expected_revision=expected_revision,
            )

        accepted, result = await self._execute_if_accepting(candidate, undo)
        if not accepted:
            raise RuntimePersistenceInvariantError(
                "meme candidate runtime scope is stale"
            )
        assert isinstance(result, ModeMeme)
        return result

    async def revoke_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return await self._change_meme_state(
            namespace_id,
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.REVOKED,
            action="revoked",
        )

    async def disable_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return await self._change_meme_state(
            namespace_id,
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.DISABLED,
            action="disabled",
        )

    async def restore_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return await self._change_meme_state(
            namespace_id,
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.ACTIVE,
            action="restored",
        )

    async def restart_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return await self.restore_meme(
            namespace_id,
            meme_id,
            expected_revision=expected_revision,
        )

    async def archive_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return await self._change_meme_state(
            namespace_id,
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.ARCHIVED,
            action="archived",
        )

    async def pin_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return await self._set_meme_pinned(
            namespace_id,
            meme_id,
            expected_revision=expected_revision,
            pinned=True,
        )

    async def unpin_meme(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
    ) -> ModeMeme:
        return await self._set_meme_pinned(
            namespace_id,
            meme_id,
            expected_revision=expected_revision,
            pinned=False,
        )

    async def _change_meme_state(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
        state: ModeMemeState,
        action: str,
    ) -> ModeMeme:
        async with self._session_factory() as session:
            repository = SQLiteModeMemeServiceRepository(session)
            await self._assert_meme_namespace(repository, namespace_id, meme_id)
            result = await repository.change_state(
                meme_id,
                expected_revision=expected_revision,
                state=state,
                action=action,
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return result

    async def _set_meme_pinned(
        self,
        namespace_id: str,
        meme_id: str,
        *,
        expected_revision: int,
        pinned: bool,
    ) -> ModeMeme:
        async with self._session_factory() as session:
            repository = SQLiteModeMemeServiceRepository(session)
            await self._assert_meme_namespace(repository, namespace_id, meme_id)
            result = await repository.set_pinned(
                meme_id,
                expected_revision=expected_revision,
                pinned=pinned,
                now_ms=self._clock.now_ms(),
            )
            await session.commit()
            return result

    @staticmethod
    async def _assert_meme_namespace(
        repository: SQLiteModeMemeServiceRepository,
        namespace_id: str,
        meme_id: str,
    ) -> None:
        meme = await repository.get(meme_id)
        if meme.namespace_id != namespace_id:
            raise RuntimePersistenceInvariantError(
                "mode meme does not belong to the requested namespace"
            )

    async def _accepts(
        self,
        candidate: RoomMemoryCandidate | MemeCandidate,
    ) -> bool:
        return await self._runtime_state.accepts(
            room_id=candidate.room_id,
            session_id=candidate.session_id,
            audience_epoch=candidate.audience_epoch,
            namespace_id=(
                candidate.namespace_id
                if isinstance(candidate, MemeCandidate)
                else None
            ),
        )

    async def _validate_meme_candidate_provenance(
        self,
        candidate: MemeCandidate,
    ) -> None:
        if self._observation_provenance is None or self._room_service is None:
            raise RuntimePersistenceInvariantError(
                "observation provenance is unavailable"
            )
        wave = self._observation_provenance.observation_wave(
            candidate.observation_id
        )
        if (
            wave is None
            or wave.room_id != candidate.room_id
            or wave.session_id != candidate.session_id
            or wave.audience_epoch != candidate.audience_epoch
        ):
            raise RuntimePersistenceInvariantError(
                "meme candidate observation provenance is invalid"
            )
        allowed_event_ids = {*wave.event_ids, *wave.trigger_event_ids}
        if not set(candidate.evidence_event_ids).issubset(allowed_event_ids):
            raise RuntimePersistenceInvariantError(
                "meme candidate event provenance is invalid"
            )
        if any(
            index < 0 or index >= len(wave.frame_hashes)
            for index in candidate.evidence_frame_indexes
        ):
            raise RuntimePersistenceInvariantError(
                "meme candidate frame provenance is invalid"
            )
        active_events = await self._room_service.read_events(candidate.session_id)
        active_event_ids = {event.event_id for event in active_events}
        if not set(candidate.evidence_event_ids).issubset(active_event_ids):
            raise RuntimePersistenceInvariantError(
                "meme candidate event provenance is unavailable"
            )

    async def _execute_if_accepting(
        self,
        candidate: RoomMemoryCandidate | MemeCandidate,
        operation: Callable[[], Awaitable[object]],
    ) -> tuple[bool, object | None]:
        return await self._runtime_state.execute_if_accepting(
            room_id=candidate.room_id,
            session_id=candidate.session_id,
            audience_epoch=candidate.audience_epoch,
            namespace_id=(
                candidate.namespace_id
                if isinstance(candidate, MemeCandidate)
                else None
            ),
            operation=operation,
        )


class _AcceptedFence:
    async def accepts(self, **scope: object) -> bool:
        del scope
        return True

    async def execute_if_accepting(
        self,
        *,
        operation: Callable[[], Awaitable[object]],
        **scope: object,
    ) -> tuple[bool, object | None]:
        del scope
        return True, await operation()
