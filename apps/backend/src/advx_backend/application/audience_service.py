import asyncio
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from advx_backend.application.builtin_audiences import builtin_profiles
from advx_backend.application.ports.generation import AudienceSnapshot
from advx_backend.application.ports.persistence import UnitOfWork, UnitOfWorkFactory
from advx_backend.application.ports.session import Clock
from advx_backend.contracts.audience import AudienceMember
from advx_backend.contracts.audience import AudienceMemory as AudienceMemoryContext
from advx_backend.contracts.generation import AudienceContext, Observation
from advx_backend.domain.audience import (
    AudienceMemory,
    AudienceProfile,
    HostRelationship,
    MemoryState,
    PeerRelationship,
)
from advx_backend.domain.session import SessionAudience


class AudienceServiceError(RuntimeError):
    pass


class AudienceSessionAlreadyActiveError(AudienceServiceError):
    def __init__(self, active_session_id: str) -> None:
        self.active_session_id = active_session_id
        super().__init__(f"audience session {active_session_id} is already active")


class AudienceSessionNotActiveError(AudienceServiceError):
    def __init__(self, session_id: str, active_session_id: str | None) -> None:
        self.session_id = session_id
        self.active_session_id = active_session_id
        if active_session_id is None:
            detail = "there is no active audience session"
        else:
            detail = f"active audience session is {active_session_id}"
        super().__init__(f"audience session {session_id} is not active; {detail}")


class AudienceService:
    """Provides a committed, session-scoped audience snapshot to generation work."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        max_memories_per_audience: int = 12,
    ) -> None:
        if max_memories_per_audience < 0:
            raise ValueError("max_memories_per_audience must not be negative")

        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._max_memories_per_audience = max_memories_per_audience
        self._active_session_id: str | None = None
        self._contexts: tuple[AudienceContext, ...] = ()
        self._lock = asyncio.Lock()

    async def initialize_builtin_audiences(self) -> tuple[AudienceProfile, ...]:
        """Add missing built-ins without modifying any existing profile."""
        async with self._lock:
            candidates = builtin_profiles(now_ms=self._clock.now_ms())
            created: list[AudienceProfile] = []
            async with self._unit_of_work_factory() as unit_of_work:
                for profile in candidates:
                    if await unit_of_work.audiences.get(profile.audience_id) is None:
                        await unit_of_work.audiences.add(profile)
                        created.append(profile)
                await unit_of_work.commit()
            return tuple(created)

    async def start_session(self, session_id: str) -> None:
        """Load and commit a fresh audience snapshot before exposing it in memory."""
        self._validate_session_id(session_id)
        async with self._lock:
            if self._active_session_id is not None:
                if self._active_session_id == session_id:
                    return
                raise AudienceSessionAlreadyActiveError(self._active_session_id)

            contexts = await self._load_session_contexts(session_id)
            self._active_session_id = session_id
            self._contexts = contexts

    async def stop_session(self, session_id: str) -> None:
        """Discard only the matching session's in-memory snapshot."""
        async with self._lock:
            if self._active_session_id != session_id:
                return
            self._active_session_id = None
            self._contexts = ()

    async def get_snapshot(self, *, observation: Observation) -> AudienceSnapshot:
        """Return a copy of the active cached contexts without reading persistence."""
        async with self._lock:
            if self._active_session_id != observation.session_id:
                raise AudienceSessionNotActiveError(
                    observation.session_id,
                    self._active_session_id,
                )
            contexts = tuple(context.model_copy(deep=True) for context in self._contexts)

        return AudienceSnapshot(
            session_id=observation.session_id,
            observation_id=observation.observation_id,
            audiences=contexts,
        )

    async def _load_session_contexts(self, session_id: str) -> tuple[AudienceContext, ...]:
        now_ms = self._clock.now_ms()
        async with self._unit_of_work_factory() as unit_of_work:
            profiles = sorted(
                await unit_of_work.audiences.list_enabled(),
                key=lambda profile: profile.audience_id,
            )
            contexts = await self._build_contexts(unit_of_work, profiles, now_ms=now_ms)
            existing = await unit_of_work.sessions.list_audiences(session_id)
            expected = {
                (profile.audience_id, profile.revision)
                for profile in profiles
            }
            restored = {
                (audience.audience_id, audience.profile_revision)
                for audience in existing
                if audience.left_at_ms is None
            }
            if restored != expected:
                for profile in profiles:
                    await unit_of_work.sessions.add_audience(
                        SessionAudience(
                            session_id=session_id,
                            audience_id=profile.audience_id,
                            profile_revision=profile.revision,
                            joined_at_ms=now_ms,
                        )
                    )
            await unit_of_work.commit()
        return contexts

    async def _build_contexts(
        self,
        unit_of_work: UnitOfWork,
        profiles: Sequence[AudienceProfile],
        *,
        now_ms: int,
    ) -> tuple[AudienceContext, ...]:
        contexts: list[AudienceContext] = []
        for profile in profiles:
            host = await unit_of_work.relationships.get_host(profile.audience_id)
            peers = await unit_of_work.relationships.list_peers(profile.audience_id)
            memories = await self._load_memories(
                unit_of_work,
                profile.audience_id,
                now_ms=now_ms,
            )
            contexts.append(
                AudienceContext(
                    member=AudienceMember(
                        audience_id=profile.audience_id,
                        display_name=profile.display_name,
                        avatar_ref=profile.avatar_ref,
                        personality=deepcopy(profile.personality),
                        preferences=deepcopy(profile.preferences),
                        speaking_style=deepcopy(profile.speaking_style),
                        relationships=self._relationship_context(
                            audience_id=profile.audience_id,
                            host=host,
                            peers=peers,
                        ),
                        enabled=profile.enabled,
                    ),
                    memories=memories,
                )
            )
        return tuple(contexts)

    async def _load_memories(
        self,
        unit_of_work: UnitOfWork,
        audience_id: str,
        *,
        now_ms: int,
    ) -> list[AudienceMemoryContext]:
        if self._max_memories_per_audience == 0:
            return []

        loaded = await unit_of_work.memories.list_active(
            audience_id,
            now_ms=now_ms,
            limit=self._max_memories_per_audience,
        )
        owned_memories = [
            memory
            for memory in loaded
            if self._is_active_owned_memory(memory, audience_id, now_ms=now_ms)
        ][: self._max_memories_per_audience]
        contexts: list[AudienceMemoryContext] = []
        for memory in owned_memories:
            evidence = await unit_of_work.memories.evidence_for(audience_id, memory.memory_id)
            contexts.append(
                AudienceMemoryContext(
                    memory_id=memory.memory_id,
                    audience_id=audience_id,
                    content=memory.content,
                    source_event_ids=[item.source_event_id for item in evidence],
                    created_at_ms=memory.created_at_ms,
                    updated_at_ms=memory.updated_at_ms,
                )
            )
        return contexts

    @staticmethod
    def _is_active_owned_memory(
        memory: AudienceMemory,
        audience_id: str,
        *,
        now_ms: int,
    ) -> bool:
        return (
            memory.audience_id == audience_id
            and memory.state is MemoryState.ACTIVE
            and (memory.expires_at_ms is None or memory.expires_at_ms > now_ms)
        )

    @staticmethod
    def _relationship_context(
        *,
        audience_id: str,
        host: HostRelationship | None,
        peers: Sequence[PeerRelationship],
    ) -> dict[str, Any]:
        relationships: dict[str, Any] = {}
        if host is not None and host.audience_id == audience_id:
            relationships["host"] = AudienceService._relationship_payload(host)

        peer_context = {
            peer.peer_audience_id: {
                "audience_id": peer.peer_audience_id,
                **AudienceService._relationship_payload(peer),
            }
            for peer in sorted(peers, key=lambda item: item.peer_audience_id)
            if peer.audience_id == audience_id
        }
        if peer_context:
            relationships["peers"] = peer_context
        return relationships

    @staticmethod
    def _relationship_payload(
        relationship: HostRelationship | PeerRelationship,
    ) -> dict[str, Any]:
        return {
            "summary": relationship.summary,
            "state": deepcopy(relationship.state),
            "source_memory_id": relationship.source_memory_id,
            "updated_by": relationship.updated_by.value,
            "revision": relationship.revision,
            "updated_at_ms": relationship.updated_at_ms,
        }

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
