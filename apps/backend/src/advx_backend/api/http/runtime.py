from typing import Annotated, NoReturn, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi import status as http_status

from advx_backend.api.dependencies import (
    LocalTokenGuard,
    RuntimeProtocolVersionGuard,
)
from advx_backend.application.runtime_config_service import RuntimeApplyError
from advx_backend.application.runtime_session_service import (
    RuntimeSessionConflictError,
    RuntimeSessionNotFoundError,
    RuntimeSessionRecoveryError,
)
from advx_backend.application.viewer_audience_service import (
    ViewerAudienceError,
    ViewerNotFoundError,
)
from advx_backend.contracts.audience import (
    MuteViewerRequest,
    SessionAudienceSnapshot,
    ViewerCommandRequest,
    ViewerSnapshot,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.contracts.session import (
    RuntimeSessionSnapshot,
    RuntimeSessionStartRequest,
)
from advx_backend.contracts.viewer_runtime import (
    RuntimeApplyRequest,
    RuntimeRollbackRequest,
)

SessionId = Annotated[str, Path(min_length=1, max_length=128)]
ViewerId = Annotated[str, Path(min_length=1, max_length=128)]


class RuntimeSessionOperations(Protocol):
    async def start(
        self,
        request: RuntimeSessionStartRequest,
    ) -> RuntimeSessionSnapshot: ...

    async def current(self, session_id: str) -> RuntimeSessionSnapshot: ...

    async def apply(
        self,
        session_id: str,
        request: RuntimeApplyRequest,
    ) -> RuntimeSessionSnapshot: ...

    async def rollback(
        self,
        session_id: str,
        request: RuntimeRollbackRequest,
    ) -> RuntimeSessionSnapshot: ...

    async def recover(self, session_id: str) -> RuntimeSessionSnapshot: ...


async def get_runtime_session_service(request: Request) -> RuntimeSessionOperations:
    service = getattr(request.app.state, "runtime_session_service", None)
    if service is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "runtime_session_service_unavailable",
                "message": "The runtime session service is not configured.",
            },
        )
    return cast(RuntimeSessionOperations, service)


async def get_viewer_audience_service(request: Request) -> object:
    service = getattr(request.app.state, "viewer_audience_service", None)
    if service is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "viewer_audience_service_unavailable",
                "message": "The Viewer audience service is not configured.",
            },
        )
    return service


def create_runtime_router(*, local_token: str) -> APIRouter:
    router = APIRouter(
        prefix="/runtime",
        tags=["runtime"],
        dependencies=[
            Depends(LocalTokenGuard(local_token)),
            Depends(RuntimeProtocolVersionGuard(PROTOCOL_VERSION)),
        ],
    )
    service_dependency = Depends(get_runtime_session_service)
    audience_dependency = Depends(get_viewer_audience_service)

    @router.post(
        "/sessions",
        response_model=RuntimeSessionSnapshot,
        status_code=http_status.HTTP_201_CREATED,
    )
    async def start_runtime_session(
        request: RuntimeSessionStartRequest,
        service: Annotated[RuntimeSessionOperations, service_dependency],
    ) -> RuntimeSessionSnapshot:
        try:
            return await service.start(request)
        except Exception as error:
            _raise_runtime_error(error, operation="start")

    @router.get("/sessions/{session_id}", response_model=RuntimeSessionSnapshot)
    async def current_runtime_session(
        session_id: SessionId,
        service: Annotated[RuntimeSessionOperations, service_dependency],
    ) -> RuntimeSessionSnapshot:
        try:
            return await service.current(session_id)
        except Exception as error:
            _raise_runtime_error(error, operation="current")

    @router.post(
        "/sessions/{session_id}/apply",
        response_model=RuntimeSessionSnapshot,
    )
    async def apply_runtime(
        session_id: SessionId,
        request: RuntimeApplyRequest,
        service: Annotated[RuntimeSessionOperations, service_dependency],
    ) -> RuntimeSessionSnapshot:
        try:
            return await service.apply(session_id, request)
        except Exception as error:
            _raise_runtime_error(error, operation="apply")

    @router.post(
        "/sessions/{session_id}/rollback",
        response_model=RuntimeSessionSnapshot,
    )
    async def rollback_runtime(
        session_id: SessionId,
        request: RuntimeRollbackRequest,
        service: Annotated[RuntimeSessionOperations, service_dependency],
    ) -> RuntimeSessionSnapshot:
        try:
            return await service.rollback(session_id, request)
        except Exception as error:
            _raise_runtime_error(error, operation="rollback")

    @router.post(
        "/sessions/{session_id}/recover",
        response_model=RuntimeSessionSnapshot,
    )
    async def recover_runtime(
        session_id: SessionId,
        service: Annotated[RuntimeSessionOperations, service_dependency],
    ) -> RuntimeSessionSnapshot:
        try:
            return await service.recover(session_id)
        except Exception as error:
            _raise_runtime_error(error, operation="recover")

    @router.get(
        "/sessions/{session_id}/audience",
        response_model=SessionAudienceSnapshot,
    )
    async def current_audience(
        session_id: SessionId,
        service: Annotated[object, audience_dependency],
    ) -> SessionAudienceSnapshot:
        try:
            return await service.current(session_id)  # type: ignore[attr-defined, no-any-return]
        except Exception as error:
            _raise_viewer_error(error)

    @router.post(
        "/sessions/{session_id}/viewers/{viewer_id}/mute",
        response_model=ViewerSnapshot,
    )
    async def mute_viewer(
        session_id: SessionId,
        viewer_id: ViewerId,
        body: MuteViewerRequest,
        service: Annotated[object, audience_dependency],
    ) -> ViewerSnapshot:
        try:
            return await service.mute(  # type: ignore[attr-defined, no-any-return]
                session_id,
                viewer_id,
                command_id=body.command_id,
                duration_ms=body.duration_ms,
                reason=body.reason,
            )
        except Exception as error:
            _raise_viewer_error(error)

    @router.post(
        "/sessions/{session_id}/viewers/{viewer_id}/unmute",
        response_model=ViewerSnapshot,
    )
    async def unmute_viewer(
        session_id: SessionId,
        viewer_id: ViewerId,
        body: ViewerCommandRequest,
        service: Annotated[object, audience_dependency],
    ) -> ViewerSnapshot:
        try:
            return await service.unmute(  # type: ignore[attr-defined, no-any-return]
                session_id,
                viewer_id,
                command_id=body.command_id,
            )
        except Exception as error:
            _raise_viewer_error(error)

    @router.post(
        "/sessions/{session_id}/viewers/{viewer_id}/kick",
        response_model=ViewerSnapshot,
    )
    async def kick_viewer(
        session_id: SessionId,
        viewer_id: ViewerId,
        body: ViewerCommandRequest,
        service: Annotated[object, audience_dependency],
    ) -> ViewerSnapshot:
        try:
            return await service.kick(  # type: ignore[attr-defined, no-any-return]
                session_id,
                viewer_id,
                command_id=body.command_id,
                reason=body.reason,
            )
        except Exception as error:
            _raise_viewer_error(error)

    return router


def _raise_viewer_error(error: Exception) -> NoReturn:
    if isinstance(error, ViewerNotFoundError) or isinstance(error, KeyError):
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "viewer_not_found", "message": str(error)},
        ) from error
    if isinstance(error, ViewerAudienceError):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": str(error)},
        ) from error
    raise HTTPException(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "viewer_audience_persistence_unavailable",
            "message": "Viewer audience persistence is unavailable.",
        },
    ) from error


def _raise_runtime_error(error: Exception, *, operation: str) -> NoReturn:
    if isinstance(error, RuntimeSessionConflictError):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={
                "code": "client_request_conflict",
                "message": str(error),
            },
        ) from error
    if isinstance(error, RuntimeSessionNotFoundError) or isinstance(error, KeyError):
        session_id = (
            error.session_id
            if isinstance(error, RuntimeSessionNotFoundError)
            else str(error.args[0])
        )
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={
                "code": "runtime_session_not_found",
                "message": f"Runtime session {session_id} was not found.",
                "session_id": session_id,
            },
        ) from error
    if isinstance(error, RuntimeSessionRecoveryError):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail={
                "code": "runtime_recovery_rejected",
                "message": str(error),
            },
        ) from error
    if operation in {"start", "apply", "rollback"} and isinstance(
        error, (RuntimeApplyError, ValueError)
    ):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": (
                    "runtime_start_rejected"
                    if operation == "start"
                    else (
                        "runtime_apply_rejected"
                        if operation == "apply"
                        else "runtime_rollback_rejected"
                    )
                ),
                "message": str(error),
            },
        ) from error
    raise HTTPException(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "runtime_persistence_unavailable",
            "message": "Runtime persistence is unavailable.",
        },
    ) from error
