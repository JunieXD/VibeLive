from pydantic import BaseModel, ConfigDict, Field, model_validator

from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    RuntimeDiffSummary,
)
from advx_backend.domain.session import SessionState, SessionStatus
from advx_backend.domain.viewer import ViewerInstance


class SessionSnapshot(BaseModel):
    session_id: str | None
    state: SessionState
    started_at_ms: int | None
    updated_at_ms: int
    revision: int

    @classmethod
    def from_domain(cls, status: SessionStatus) -> "SessionSnapshot":
        return cls(
            session_id=status.session_id,
            state=status.state,
            started_at_ms=status.started_at_ms,
            updated_at_ms=status.updated_at_ms,
            revision=status.revision,
        )


class RuntimeSessionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeSessionStartRequest(RuntimeSessionContract):
    client_request_id: str = Field(min_length=1, max_length=128)
    canonical_runtime_spec: CanonicalRuntimeSpec
    client_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_config_hash(self) -> "RuntimeSessionStartRequest":
        if self.client_config_hash != self.canonical_runtime_spec.config_hash():
            raise ValueError("client_config_hash does not match canonical_runtime_spec")
        return self


class RuntimeSessionSnapshot(RuntimeSessionContract):
    session_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    config_revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_runtime_spec: CanonicalRuntimeSpec
    viewers: list[ViewerInstance] = Field(default_factory=list, max_length=32)
    apply_id: str | None = Field(default=None, min_length=1, max_length=128)
    diff: RuntimeDiffSummary = Field(default_factory=RuntimeDiffSummary)
    recovered: bool = False
