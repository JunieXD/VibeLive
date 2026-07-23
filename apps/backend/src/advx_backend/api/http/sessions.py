from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi import status as http_status

from advx_backend.api.dependencies import LocalTokenGuard, ProtocolVersionGuard
from advx_backend.application.session_service import (
    InvalidSessionStateError,
    SessionAlreadyActiveError,
    SessionError,
    SessionNotFoundError,
    SessionPersistenceError,
    SessionService,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.contracts.session import (
    SessionSnapshot,
    SessionStartRequest,
    SessionStartResponse,
)

SessionId = Annotated[str, Path(min_length=1, max_length=128)]


def create_session_router(
    *,
    session_service: SessionService,
    local_token: str,
) -> APIRouter:
    token_guard = LocalTokenGuard(local_token)
    version_guard = ProtocolVersionGuard(PROTOCOL_VERSION)
    router = APIRouter(
        prefix="/sessions",
        tags=["sessions"],
        dependencies=[Depends(token_guard), Depends(version_guard)],
    )

    @router.get("/current", response_model=SessionSnapshot)
    async def current_session() -> SessionSnapshot:
        return SessionSnapshot.from_domain(await session_service.status())

    @router.post(
        "",
        response_model=SessionStartResponse,
        status_code=http_status.HTTP_201_CREATED,
    )
    async def start_session(request: SessionStartRequest) -> SessionStartResponse:
        try:
            status = await session_service.start()
        except SessionError as error:
            _raise_http_error(error)
        return SessionStartResponse.from_domain(status, request)

    @router.post("/{session_id}/pause", response_model=SessionSnapshot)
    async def pause_session(session_id: SessionId) -> SessionSnapshot:
        try:
            status = await session_service.pause(session_id)
        except SessionError as error:
            _raise_http_error(error)
        return SessionSnapshot.from_domain(status)

    @router.post("/{session_id}/resume", response_model=SessionSnapshot)
    async def resume_session(session_id: SessionId) -> SessionSnapshot:
        try:
            status = await session_service.resume(session_id)
        except SessionError as error:
            _raise_http_error(error)
        return SessionSnapshot.from_domain(status)

    @router.post("/{session_id}/stop", response_model=SessionSnapshot)
    async def stop_session(session_id: SessionId) -> SessionSnapshot:
        try:
            status = await session_service.stop(session_id)
        except SessionError as error:
            _raise_http_error(error)
        return SessionSnapshot.from_domain(status)

    return router


def _raise_http_error(error: SessionError) -> NoReturn:
    if isinstance(error, SessionAlreadyActiveError):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={
                "code": "session_already_active",
                "message": str(error),
                "session_id": error.status.session_id,
                "state": error.status.state,
            },
        ) from error
    if isinstance(error, SessionNotFoundError):
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={
                "code": "session_not_found",
                "message": str(error),
                "session_id": error.session_id,
            },
        ) from error
    if isinstance(error, InvalidSessionStateError):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_session_state",
                "message": str(error),
                "session_id": error.status.session_id,
                "state": error.status.state,
                "allowed_states": sorted(state.value for state in error.allowed_states),
            },
        ) from error
    if isinstance(error, SessionPersistenceError):
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persistence_unavailable",
                "message": "Local persistence is unavailable.",
            },
        ) from error
    raise HTTPException(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "session_error", "message": "Session operation failed."},
    ) from error
