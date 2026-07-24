from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from advx_backend.contracts.protocol import PROTOCOL_VERSION

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    protocol_version: Literal[2] = PROTOCOL_VERSION
    persistence_error: dict[str, str] | None = None


@router.get(
    "/health",
    response_model=HealthResponse,
    response_model_exclude_none=True,
)
async def health(request: Request) -> HealthResponse:
    runtime = getattr(request.app.state, "runtime", None)
    database = getattr(runtime, "database", None)
    error = None if database is None else database.startup_error
    if error is not None:
        return HealthResponse(status="degraded", persistence_error=error)
    if database is not None and not database.started:
        return HealthResponse(
            status="degraded",
            persistence_error={
                "code": "sqlite_not_started",
                "message": "SQLite persistence is not started.",
                "backup_path": "",
            },
        )
    return HealthResponse()
