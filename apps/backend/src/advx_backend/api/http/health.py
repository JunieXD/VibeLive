from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from advx_backend.contracts.protocol import PROTOCOL_VERSION

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    protocol_version: Literal[1] = PROTOCOL_VERSION


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()
