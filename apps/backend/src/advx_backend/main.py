from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from advx_backend.api.http.health import router as health_router
from advx_backend.api.http.sessions import create_session_router
from advx_backend.api.ws.realtime import create_realtime_router
from advx_backend.bootstrap import (
    BACKEND_VERSION,
    BackendRuntime,
    build_runtime,
    build_runtime_from_environment,
)
from advx_backend.contracts.openapi import add_realtime_schemas


def create_app(*, runtime: BackendRuntime | None = None) -> FastAPI:
    active_runtime = build_runtime() if runtime is None else runtime

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await active_runtime.startup()
            yield
        finally:
            await active_runtime.shutdown()

    application = FastAPI(
        title="ADVX Live Backend",
        version=BACKEND_VERSION,
        lifespan=lifespan,
    )
    application.state.runtime = active_runtime
    application.include_router(health_router)
    application.include_router(
        create_session_router(
            session_service=active_runtime.session_service,
            local_token=active_runtime.local_token,
        )
    )
    application.include_router(
        create_realtime_router(
            session_service=active_runtime.session_service,
            broker=active_runtime.realtime_broker,
            ingest_gateway=active_runtime.ingest_gateway,
            local_token=active_runtime.local_token,
        )
    )

    def openapi() -> dict[str, Any]:
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title,
                version=application.version,
                routes=application.routes,
            )
            application.openapi_schema = add_realtime_schemas(schema)
        return application.openapi_schema

    application.openapi = openapi
    return application


app = create_app(runtime=build_runtime_from_environment())
