from typing import Annotated, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field

from advx_backend.api.dependencies import (
    LocalTokenGuard,
    RuntimeProtocolVersionGuard,
)
from advx_backend.application.ports.memory import RoomMemoryCandidate
from advx_backend.application.shared_brain_service import SharedBrainService
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.domain.meme import MemeCandidate, ModeMeme
from advx_backend.domain.memory import RoomLongTermMemory, RoomMemoryType
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    RuntimePersistenceConflictError,
    RuntimePersistenceInvariantError,
)

ScopedId = Annotated[str, Path(min_length=1, max_length=128)]


class SharedBrainApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedRevisionRequest(SharedBrainApiModel):
    expected_revision: int = Field(ge=0)


class AutoIngestRequest(ExpectedRevisionRequest):
    enabled: bool


class AutoIngestResponse(SharedBrainApiModel):
    namespace_id: str
    enabled: bool
    revision: int


class LegacyMemeImportRequest(SharedBrainApiModel):
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    legacy_meme_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)
    legacy_created_at_ms: int | None = Field(default=None, ge=0)


class LegacyMemeImportResponse(SharedBrainApiModel):
    candidate_id: str
    meme_id: str
    provenance_event_id: str
    created: bool


class MemoryCandidateRequest(SharedBrainApiModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    base_revision: int = Field(ge=0)
    memory_id: str = Field(min_length=1, max_length=128)
    memory_type: RoomMemoryType
    content: str = Field(min_length=1, max_length=4_000)
    evidence_event_ids: list[str] = Field(min_length=1, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=64)
    origin: str = Field(default="extracted", min_length=1, max_length=64)
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)

    def to_candidate(self) -> RoomMemoryCandidate:
        return RoomMemoryCandidate(
            candidate_id=self.candidate_id,
            room_id=self.room_id,
            session_id=self.session_id,
            audience_epoch=self.audience_epoch,
            idempotency_key=self.idempotency_key,
            base_revision=self.base_revision,
            memory_id=self.memory_id,
            memory_type=self.memory_type,
            content=self.content,
            evidence_event_ids=tuple(self.evidence_event_ids),
            tags=tuple(self.tags),
            origin=self.origin,
            importance=self.importance,
            confidence=self.confidence,
        )


class CandidateCommitResponse(SharedBrainApiModel):
    accepted: bool
    pending: bool = False
    result_id: str | None = None
    revision: int | None = None
    head_revision: int | None = None
    created: bool = False
    reason: str | None = None


class MemoryResetResponse(SharedBrainApiModel):
    deleted_count: int


class MemoryHeadResponse(SharedBrainApiModel):
    room_id: str
    revision: int = Field(ge=0)


class MemoryEditRequest(ExpectedRevisionRequest):
    content: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_event_ids: list[str] | None = Field(default=None, max_length=128)


class MemoryMergeRequest(ExpectedRevisionRequest):
    source_memory_id: str = Field(min_length=1, max_length=128)
    source_expected_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=4_000)


class MemoryReplaceRequest(ExpectedRevisionRequest):
    replacement_memory_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4_000)
    evidence_event_ids: list[str] = Field(min_length=1, max_length=128)


class MemeEditRequest(ExpectedRevisionRequest):
    text: str = Field(min_length=1, max_length=500)
    intensity: float | None = Field(default=None, ge=0, le=1)


class MemeMaintenanceResponse(SharedBrainApiModel):
    archived_meme_ids: list[str]


async def get_shared_brain_service(request: Request) -> SharedBrainService:
    service = getattr(request.app.state, "shared_brain_service", None)
    if service is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "shared_brain_service_unavailable",
                "message": "The Shared Brain service is not configured.",
            },
        )
    return cast(SharedBrainService, service)


def create_shared_brain_router(*, local_token: str) -> APIRouter:
    router = APIRouter(
        prefix="/shared-brain",
        tags=["shared-brain"],
        dependencies=[
            Depends(LocalTokenGuard(local_token)),
            Depends(RuntimeProtocolVersionGuard(PROTOCOL_VERSION)),
        ],
    )
    service_dependency = Depends(get_shared_brain_service)

    @router.get(
        "/rooms/{room_id}/memories",
        response_model=list[RoomLongTermMemory],
    )
    async def list_memories(
        room_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> tuple[RoomLongTermMemory, ...]:
        return await service.list_memories(room_id)

    @router.get(
        "/rooms/{room_id}/memory-head",
        response_model=MemoryHeadResponse,
    )
    async def get_memory_head(
        room_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> MemoryHeadResponse:
        try:
            revision = await service.get_memory_head(room_id)
        except Exception as error:
            _raise_persistence_error(error)
        return MemoryHeadResponse(room_id=room_id, revision=revision)

    @router.get(
        "/rooms/{room_id}/memories/{memory_id}",
        response_model=RoomLongTermMemory,
    )
    async def get_memory(
        room_id: ScopedId,
        memory_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> RoomLongTermMemory:
        try:
            return await service.get_memory(room_id, memory_id)
        except Exception as error:
            _raise_persistence_error(error)

    @router.put(
        "/rooms/{room_id}/memories/{memory_id}",
        response_model=RoomLongTermMemory,
    )
    async def edit_memory(
        room_id: ScopedId,
        memory_id: ScopedId,
        body: MemoryEditRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> RoomLongTermMemory:
        try:
            return await service.edit_memory(
                room_id,
                memory_id,
                expected_revision=body.expected_revision,
                content=body.content,
                confidence=body.confidence,
                evidence_event_ids=(
                    None
                    if body.evidence_event_ids is None
                    else tuple(body.evidence_event_ids)
                ),
            )
        except Exception as error:
            _raise_persistence_error(error)

    @router.post(
        "/modes/{namespace_id}/memes/maintenance",
        response_model=MemeMaintenanceResponse,
    )
    async def maintain_memes(
        namespace_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> MemeMaintenanceResponse:
        try:
            archived = await service.maintain_memes(namespace_id)
        except Exception as error:
            _raise_persistence_error(error)
        return MemeMaintenanceResponse(archived_meme_ids=list(archived))

    @router.post(
        "/rooms/{room_id}/memories/{memory_id}/merge",
        response_model=RoomLongTermMemory,
    )
    async def merge_memory(
        room_id: ScopedId,
        memory_id: ScopedId,
        body: MemoryMergeRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> RoomLongTermMemory:
        try:
            return await service.merge_memory(
                room_id,
                memory_id,
                body.source_memory_id,
                expected_revision=body.expected_revision,
                source_expected_revision=body.source_expected_revision,
                content=body.content,
            )
        except Exception as error:
            _raise_persistence_error(error)

    @router.post(
        "/rooms/{room_id}/memories/{memory_id}/replace",
        response_model=RoomLongTermMemory,
    )
    async def replace_memory(
        room_id: ScopedId,
        memory_id: ScopedId,
        body: MemoryReplaceRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> RoomLongTermMemory:
        try:
            return await service.replace_memory(
                room_id,
                memory_id,
                expected_revision=body.expected_revision,
                replacement_memory_id=body.replacement_memory_id,
                content=body.content,
                evidence_event_ids=tuple(body.evidence_event_ids),
            )
        except Exception as error:
            _raise_persistence_error(error)

    @router.post(
        "/memory-candidates",
        response_model=CandidateCommitResponse,
    )
    async def commit_memory_candidate(
        body: MemoryCandidateRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> CandidateCommitResponse:
        try:
            result = await service.commit_memory_candidate(body.to_candidate())
        except Exception as error:
            _raise_persistence_error(error)
        return CandidateCommitResponse(
            accepted=result.accepted,
            result_id=result.memory_id,
            revision=result.memory_revision,
            head_revision=result.head_revision,
            created=result.created,
            reason=result.reason,
        )

    @router.post(
        "/rooms/{room_id}/memories/{memory_id}/revoke",
        response_model=RoomLongTermMemory,
    )
    async def revoke_memory(
        room_id: ScopedId,
        memory_id: ScopedId,
        body: ExpectedRevisionRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> RoomLongTermMemory:
        try:
            return await service.revoke_memory(
                room_id,
                memory_id,
                expected_revision=body.expected_revision,
            )
        except Exception as error:
            _raise_persistence_error(error)

    @router.delete("/rooms/{room_id}/memories/{memory_id}")
    async def delete_memory(
        room_id: ScopedId,
        memory_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
        expected_revision: Annotated[int, Query(ge=1)],
    ) -> dict[str, bool]:
        try:
            deleted = await service.delete_memory(
                room_id,
                memory_id,
                expected_revision=expected_revision,
            )
        except Exception as error:
            _raise_persistence_error(error)
        if not deleted:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={"code": "memory_not_found", "memory_id": memory_id},
            )
        return {"deleted": True}

    @router.post(
        "/rooms/{room_id}/memories/reset",
        response_model=MemoryResetResponse,
    )
    async def reset_memories(
        room_id: ScopedId,
        body: ExpectedRevisionRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> MemoryResetResponse:
        try:
            count = await service.reset_memories(
                room_id,
                expected_revision=body.expected_revision,
            )
        except Exception as error:
            _raise_persistence_error(error)
        return MemoryResetResponse(deleted_count=count)

    @router.get(
        "/modes/{namespace_id}/memes",
        response_model=list[ModeMeme],
    )
    async def list_memes(
        namespace_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
        active_only: bool = False,
    ) -> tuple[ModeMeme, ...]:
        if active_only:
            return await service.list_active_memes(namespace_id)
        return await service.list_memes(namespace_id)

    @router.get(
        "/modes/{namespace_id}/memes/active",
        response_model=list[ModeMeme],
    )
    async def list_active_memes(
        namespace_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> tuple[ModeMeme, ...]:
        return await service.list_active_memes(namespace_id)

    @router.get(
        "/modes/{namespace_id}/meme-candidates/pending",
        response_model=list[MemeCandidate],
    )
    async def list_pending_candidates(
        namespace_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> tuple[MemeCandidate, ...]:
        return await service.list_pending_candidates(namespace_id)

    @router.get(
        "/modes/{namespace_id}/auto-ingest",
        response_model=AutoIngestResponse,
    )
    async def get_auto_ingest(
        namespace_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> AutoIngestResponse:
        setting = await service.get_auto_ingest(namespace_id)
        return AutoIngestResponse(**setting.__dict__)

    @router.put(
        "/modes/{namespace_id}/auto-ingest",
        response_model=AutoIngestResponse,
    )
    async def set_auto_ingest(
        namespace_id: ScopedId,
        body: AutoIngestRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> AutoIngestResponse:
        try:
            setting = await service.set_auto_ingest(
                namespace_id,
                enabled=body.enabled,
                expected_revision=body.expected_revision,
            )
        except Exception as error:
            _raise_persistence_error(error)
        return AutoIngestResponse(**setting.__dict__)

    @router.post(
        "/meme-candidates",
        response_model=CandidateCommitResponse,
    )
    async def commit_meme_candidate(
        body: MemeCandidate,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> CandidateCommitResponse:
        try:
            result = await service.commit_meme_candidate(body)
        except Exception as error:
            _raise_persistence_error(error)
        return CandidateCommitResponse(
            accepted=result.accepted,
            pending=result.pending,
            result_id=result.meme_id,
            reason=result.reason,
        )

    @router.post(
        "/modes/{namespace_id}/legacy-memes/import",
        response_model=LegacyMemeImportResponse,
    )
    async def import_legacy_meme(
        namespace_id: ScopedId,
        body: LegacyMemeImportRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> LegacyMemeImportResponse:
        try:
            result = await service.import_legacy_meme(
                namespace_id,
                room_id=body.room_id,
                session_id=body.session_id,
                audience_epoch=body.audience_epoch,
                legacy_meme_id=body.legacy_meme_id,
                text=body.text,
                legacy_created_at_ms=body.legacy_created_at_ms,
            )
        except Exception as error:
            _raise_persistence_error(error)
        return LegacyMemeImportResponse(
            candidate_id=result.candidate_id,
            meme_id=result.meme_id,
            provenance_event_id=result.provenance_event_id,
            created=result.created,
        )

    @router.post(
        "/modes/{namespace_id}/meme-candidates/{candidate_id}/approve",
        response_model=CandidateCommitResponse,
    )
    async def approve_meme_candidate(
        namespace_id: ScopedId,
        candidate_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> CandidateCommitResponse:
        try:
            result = await service.approve_meme_candidate(namespace_id, candidate_id)
        except Exception as error:
            _raise_persistence_error(error)
        return CandidateCommitResponse(
            accepted=result.accepted,
            pending=result.pending,
            result_id=result.meme_id,
            reason=result.reason,
        )

    @router.post(
        "/modes/{namespace_id}/meme-candidates/{candidate_id}/reject",
        response_model=MemeCandidate,
    )
    async def reject_meme_candidate(
        namespace_id: ScopedId,
        candidate_id: ScopedId,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> MemeCandidate:
        try:
            return await service.reject_meme_candidate(namespace_id, candidate_id)
        except Exception as error:
            _raise_persistence_error(error)

    @router.put(
        "/modes/{namespace_id}/memes/{meme_id}",
        response_model=ModeMeme,
    )
    async def edit_meme(
        namespace_id: ScopedId,
        meme_id: ScopedId,
        body: MemeEditRequest,
        service: Annotated[SharedBrainService, service_dependency],
    ) -> ModeMeme:
        try:
            return await service.edit_meme(
                namespace_id,
                meme_id,
                expected_revision=body.expected_revision,
                text=body.text,
                intensity=body.intensity,
            )
        except Exception as error:
            _raise_persistence_error(error)

    for action in (
        "undo",
        "revoke",
        "disable",
        "restore",
        "restart",
        "pin",
        "unpin",
        "archive",
    ):
        _add_meme_action(router, action)

    return router


def _add_meme_action(
    router: APIRouter,
    action: str,
) -> None:
    async def mutate(
        namespace_id: ScopedId,
        meme_id: ScopedId,
        body: ExpectedRevisionRequest,
        service: Annotated[SharedBrainService, Depends(get_shared_brain_service)],
    ) -> ModeMeme:
        operation = getattr(service, f"{action}_meme")
        try:
            return await operation(
                namespace_id,
                meme_id,
                expected_revision=body.expected_revision,
            )
        except Exception as error:
            _raise_persistence_error(error)

    router.add_api_route(
        f"/modes/{{namespace_id}}/memes/{{meme_id}}/{action}",
        mutate,
        methods=["POST"],
        response_model=ModeMeme,
        name=f"{action}_mode_meme",
    )


def _raise_persistence_error(error: Exception) -> NoReturn:
    if isinstance(error, RuntimePersistenceConflictError):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={"code": "revision_conflict", "message": str(error)},
        ) from error
    if isinstance(error, RuntimePersistenceInvariantError):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "shared_brain_invariant", "message": str(error)},
        ) from error
    raise error
