from dataclasses import dataclass
from enum import StrEnum

from advx_backend.application.ports.model import ModelProvider


class CapabilityProbeStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class CapabilityProbeCheck:
    capability: str
    status: CapabilityProbeStatus
    model_id: str | None = None
    error_code: str | None = None
    http_status: int | None = None


@dataclass(frozen=True, slots=True)
class CapabilityProbeResult:
    status: CapabilityProbeStatus
    discovered_model_ids: tuple[str, ...]
    checks: tuple[CapabilityProbeCheck, ...]


__all__ = [
    "CapabilityProbeCheck",
    "CapabilityProbeResult",
    "CapabilityProbeStatus",
    "ModelProvider",
]
