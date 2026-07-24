from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from advx_backend.domain.meme import MemeCandidate, ModeMeme, ModeMemeState


@dataclass(frozen=True)
class MemeCommitResult:
    accepted: bool
    meme_id: str | None = None
    pending: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class MemeAutoIngestSetting:
    namespace_id: str
    enabled: bool
    revision: int


class ModeMemeRepository(Protocol):
    async def list_active(self, namespace_id: str) -> tuple[ModeMeme, ...]: ...

    async def save_candidate(self, candidate: MemeCandidate) -> None: ...

    async def list_pending(self, namespace_id: str) -> tuple[MemeCandidate, ...]: ...

    async def get_auto_ingest(self, namespace_id: str) -> MemeAutoIngestSetting: ...

    async def set_auto_ingest(
        self,
        namespace_id: str,
        *,
        enabled: bool,
        expected_revision: int,
        now_ms: int,
    ) -> MemeAutoIngestSetting: ...

    async def commit_candidate(self, candidate: MemeCandidate) -> MemeCommitResult: ...

    async def approve_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
        *,
        now_ms: int,
    ) -> MemeCommitResult: ...

    async def reject_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
        *,
        now_ms: int,
    ) -> MemeCandidate: ...

    async def edit(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        text: str,
        intensity: float,
        now_ms: int,
    ) -> ModeMeme: ...

    async def change_state(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        state: ModeMemeState,
        action: str,
        now_ms: int,
    ) -> ModeMeme: ...

    async def set_pinned(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        pinned: bool,
        now_ms: int,
    ) -> ModeMeme: ...

    async def list_archive_candidates(
        self,
        namespace_id: str,
        *,
        inactive_before_ms: int,
    ) -> tuple[ModeMeme, ...]: ...


class SessionFence(Protocol):
    async def accepts(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
    ) -> bool: ...

    async def execute_if_accepting(
        self,
        *,
        room_id: str,
        session_id: str,
        audience_epoch: int,
        operation: Callable[[], Awaitable[object]],
    ) -> tuple[bool, object | None]: ...
