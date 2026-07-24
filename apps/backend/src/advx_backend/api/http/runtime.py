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

    return router


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
