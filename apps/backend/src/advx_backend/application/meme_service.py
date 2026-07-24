from advx_backend.application.ports.meme import (
    MemeCommitResult,
    ModeMemeRepository,
    SessionFence,
)
from advx_backend.application.ports.session import Clock
from advx_backend.domain.meme import (
    MemeCandidate,
    MemeCandidateOutcome,
    ModeMeme,
    ModeMemeState,
)

_AUTO_ARCHIVE_AFTER_MS = 30 * 24 * 60 * 60 * 1_000
_AUTO_ARCHIVE_MAX_USES = 3


class ModeMemeService:
    def __init__(
        self,
        *,
        repository: ModeMemeRepository,
        session_fence: SessionFence,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._session_fence = session_fence
        self._clock = clock
        self._auto_ingest: dict[str, bool] = {}

    async def list_active(self, namespace_id: str) -> tuple[ModeMeme, ...]:
        memes = await self._repository.list_active(namespace_id)
        return tuple(
            meme
            for meme in memes
            if meme.namespace_id == namespace_id and meme.state is ModeMemeState.ACTIVE
        )

    def set_auto_ingest(self, namespace_id: str, *, enabled: bool) -> None:
        self._auto_ingest[namespace_id] = enabled

    def auto_ingest_enabled(self, namespace_id: str) -> bool:
        return self._auto_ingest.get(namespace_id, True)

    async def commit_candidate(self, candidate: MemeCandidate) -> MemeCommitResult:
        if not await self._session_fence.accepts(
            room_id=candidate.room_id,
            session_id=candidate.session_id,
            audience_epoch=candidate.audience_epoch,
        ):
            return MemeCommitResult(accepted=False, reason="stale_epoch")
        if candidate.outcome is not MemeCandidateOutcome.PENDING:
            return MemeCommitResult(accepted=False, reason="candidate_not_pending")

        if not self.auto_ingest_enabled(candidate.namespace_id):
            await self._repository.save_candidate(candidate)
            return MemeCommitResult(
                accepted=False,
                pending=True,
                reason="auto_ingest_disabled",
            )

        return await self._repository.commit_candidate(candidate)

    async def approve_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
    ) -> MemeCommitResult:
        return await self._repository.approve_candidate(
            namespace_id,
            candidate_id,
            now_ms=self._clock.now_ms(),
        )

    async def reject_candidate(
        self,
        namespace_id: str,
        candidate_id: str,
    ) -> MemeCandidate:
        return await self._repository.reject_candidate(
            namespace_id,
            candidate_id,
            now_ms=self._clock.now_ms(),
        )

    async def edit(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        text: str,
        intensity: float,
    ) -> ModeMeme:
        return await self._repository.edit(
            meme_id,
            expected_revision=expected_revision,
            text=text,
            intensity=intensity,
            now_ms=self._clock.now_ms(),
        )

    async def undo(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self.revoke(meme_id, expected_revision=expected_revision)

    async def revoke(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self._change_state(
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.REVOKED,
            action="revoked",
        )

    async def disable(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self._change_state(
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.DISABLED,
            action="disabled",
        )

    async def restore(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self._change_state(
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.ACTIVE,
            action="restored",
        )

    async def restart(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self.restore(meme_id, expected_revision=expected_revision)

    async def archive(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self._change_state(
            meme_id,
            expected_revision=expected_revision,
            state=ModeMemeState.ARCHIVED,
            action="archived",
        )

    async def set_pinned(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        pinned: bool,
    ) -> ModeMeme:
        return await self._repository.set_pinned(
            meme_id,
            expected_revision=expected_revision,
            pinned=pinned,
            now_ms=self._clock.now_ms(),
        )

    async def pin(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self.set_pinned(
            meme_id,
            expected_revision=expected_revision,
            pinned=True,
        )

    async def unpin(self, meme_id: str, *, expected_revision: int) -> ModeMeme:
        return await self.set_pinned(
            meme_id,
            expected_revision=expected_revision,
            pinned=False,
        )

    async def auto_archive(self, namespace_id: str) -> tuple[str, ...]:
        now_ms = self._clock.now_ms()
        candidates = await self._repository.list_archive_candidates(
            namespace_id,
            inactive_before_ms=now_ms - _AUTO_ARCHIVE_AFTER_MS,
        )
        archived: list[str] = []
        for meme in candidates:
            if (
                meme.namespace_id != namespace_id
                or meme.state is not ModeMemeState.ACTIVE
                or meme.pinned
                or meme.use_count >= _AUTO_ARCHIVE_MAX_USES
                or meme.updated_at_ms > now_ms - _AUTO_ARCHIVE_AFTER_MS
            ):
                continue
            await self.archive(meme.meme_id, expected_revision=meme.revision)
            archived.append(meme.meme_id)
        return tuple(archived)

    async def _change_state(
        self,
        meme_id: str,
        *,
        expected_revision: int,
        state: ModeMemeState,
        action: str,
    ) -> ModeMeme:
        return await self._repository.change_state(
            meme_id,
            expected_revision=expected_revision,
            state=state,
            action=action,
            now_ms=self._clock.now_ms(),
        )
