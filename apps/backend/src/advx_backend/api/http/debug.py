from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi import status as http_status

from advx_backend.api.dependencies import (
    LocalTokenGuard,
    RuntimeProtocolVersionGuard,
)
from advx_backend.contracts.debug import (
    AiCallImagePreview,
    AiCallQuery,
    AiCallQueryResponse,
    AiCallRole,
    AiCallStatus,
    AiCallTrace,
    DebugRuntimeSnapshot,
    TraceQuery,
    TraceQueryResponse,
    TraceResponseStatus,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.contracts.replay import ReplayRequest, ReplayResult
from advx_backend.infrastructure.logging.trace_store import (
    UnsafeTraceArtifactError,
    assert_redacted_artifact,
)


class DebugServiceApi(Protocol):
    def query(self, query: TraceQuery) -> TraceQueryResponse: ...

    def query_ai_calls(self, query: AiCallQuery) -> AiCallQueryResponse: ...

    def get_ai_call(self, call_id: str) -> AiCallTrace | None: ...

    def query_ai_call_image(self, preview_id: str) -> AiCallImagePreview | None: ...

    def export_artifact(self, query: TraceQuery) -> dict[str, object]: ...

    async def runtime_snapshot(self, session_id: str) -> DebugRuntimeSnapshot: ...


class ReplayServiceApi(Protocol):
    async def replay(self, replay_request: ReplayRequest) -> ReplayResult: ...


def create_debug_router(*, local_token: str) -> APIRouter:
    router = APIRouter(
        prefix="/debug",
        tags=["debug"],
        dependencies=[
            Depends(LocalTokenGuard(local_token)),
            Depends(RuntimeProtocolVersionGuard(PROTOCOL_VERSION)),
        ],
    )

    @router.get("/traces", response_model=TraceQueryResponse)
    async def query_traces(
        request: Request,
        room_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        session_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        observation_id: Annotated[
            str | None,
            Query(min_length=1, max_length=128),
        ] = None,
        viewer_instance_id: Annotated[
            str | None,
            Query(min_length=1, max_length=128),
        ] = None,
        response_status: TraceResponseStatus | None = None,
        cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> TraceQueryResponse:
        service = _debug_service(request)
        try:
            return service.query(
                TraceQuery(
                    room_id=room_id,
                    session_id=session_id,
                    observation_id=observation_id,
                    viewer_instance_id=viewer_instance_id,
                    response_status=response_status,
                    cursor=cursor,
                    limit=limit,
                )
            )
        except ValueError as error:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_trace_query", "message": str(error)},
            ) from error

    @router.post("/traces/export")
    async def export_traces(
        request: Request,
        query: TraceQuery,
    ) -> dict[str, object]:
        try:
            return _public_export_artifact(
                _debug_service(request).export_artifact(query)
            )
        except (UnsafeTraceArtifactError, ValueError) as error:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "unsafe_trace_artifact", "message": str(error)},
            ) from error

    @router.get("/ai-calls", response_model=AiCallQueryResponse)
    async def query_ai_calls(
        request: Request,
        session_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        role: AiCallRole | None = None,
        status: AiCallStatus | None = None,
        correlation_id: Annotated[
            str | None,
            Query(min_length=1, max_length=128),
        ] = None,
        cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> AiCallQueryResponse:
        try:
            return _debug_service(request).query_ai_calls(
                AiCallQuery(
                    session_id=session_id,
                    role=role,
                    status=status,
                    correlation_id=correlation_id,
                    cursor=cursor,
                    limit=limit,
                )
            )
        except ValueError as error:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_ai_call_query", "message": str(error)},
            ) from error

    @router.get("/ai-calls/images/{preview_id}", response_model=AiCallImagePreview)
    async def query_ai_call_image(
        request: Request,
        preview_id: Annotated[str, Path(min_length=1, max_length=128)],
    ) -> AiCallImagePreview:
        preview = _debug_service(request).query_ai_call_image(preview_id)
        if preview is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "ai_call_image_not_found",
                    "message": "The AI call image preview is unavailable.",
                },
            )
        return preview

    @router.get("/ai-calls/{call_id}", response_model=AiCallTrace)
    async def query_ai_call(
        request: Request,
        call_id: Annotated[str, Path(min_length=1, max_length=128)],
    ) -> AiCallTrace:
        trace = _debug_service(request).get_ai_call(call_id)
        if trace is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "ai_call_not_found",
                    "message": "The AI call is unavailable.",
                },
            )
        return trace

    @router.get("/runtime/{session_id}", response_model=DebugRuntimeSnapshot)
    async def runtime_snapshot(
        request: Request,
        session_id: str,
    ) -> DebugRuntimeSnapshot:
        try:
            return await _debug_service(request).runtime_snapshot(session_id)
        except KeyError as error:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "runtime_session_not_found",
                    "message": "The runtime Session is not active.",
                },
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "runtime_snapshot_unavailable", "message": str(error)},
            ) from error

    @router.post("/replay", response_model=ReplayResult)
    async def replay(
        request: Request,
        replay_request: ReplayRequest,
    ) -> ReplayResult:
        try:
            return await _replay_service(request).replay(replay_request)
        except UnsafeTraceArtifactError as error:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "unsafe_replay_bundle", "message": str(error)},
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_replay_request", "message": str(error)},
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "live_replay_unavailable", "message": str(error)},
            ) from error

    return router


def _debug_service(request: Request) -> DebugServiceApi:
    service = getattr(request.app.state, "debug_service", None)
    if service is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "debug_service_unavailable",
                "message": "Debug trace service is unavailable.",
            },
        )
    return cast(DebugServiceApi, service)


def _replay_service(request: Request) -> ReplayServiceApi:
    service = getattr(request.app.state, "replay_service", None)
    if service is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "replay_service_unavailable",
                "message": "Replay service is unavailable.",
            },
        )
    return cast(ReplayServiceApi, service)


def _public_export_artifact(value: dict[str, object]) -> dict[str, object]:
    """Keep prompt metadata while ensuring exported keys never imply raw prompts."""

    def sanitize(item: object) -> object:
        if isinstance(item, dict):
            return {
                ("input_manifest" if key == "prompt_manifest" else key): sanitize(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [sanitize(child) for child in item]
        return item

    artifact = cast(dict[str, object], sanitize(value))
    assert_redacted_artifact(artifact)
    return artifact
