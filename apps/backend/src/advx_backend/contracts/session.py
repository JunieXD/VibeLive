import hashlib
import json
import unicodedata
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from advx_backend.contracts.protocol import AUDIENCE_CONTRACT_VERSION
from advx_backend.domain.session import SessionState, SessionStatus

MAX_CONTRACT_IDENTIFIER_LENGTH = 128
MAX_PERSONA_COUNT = 32
MAX_VIEWER_COUNT = 32


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivityBand(ContractModel):
    min: int = Field(ge=0, le=MAX_VIEWER_COUNT)
    max: int = Field(ge=0, le=MAX_VIEWER_COUNT)

    @model_validator(mode="after")
    def validate_order(self) -> "ActivityBand":
        if self.min > self.max:
            raise ValueError("activity band min cannot exceed max")
        return self


class PersonaTemplate(ContractModel):
    persona_id: str = Field(min_length=1, max_length=MAX_CONTRACT_IDENTIFIER_LENGTH)
    document_version: Literal[1]
    revision: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=64)
    display_color: str = Field(min_length=1, max_length=32)
    enabled: bool
    base_content: dict[str, Any]

    @field_validator("persona_id", "display_name", "display_color")
    @classmethod
    def normalize_trimmed_string(cls, value: str) -> str:
        normalized = _normalize_string(value).strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized

    @field_validator("base_content")
    @classmethod
    def normalize_content(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _normalize_json(value)


class AudienceModeConfiguration(ContractModel):
    mode_id: str = Field(min_length=1, max_length=MAX_CONTRACT_IDENTIFIER_LENGTH)
    namespace_id: str = Field(min_length=1, max_length=MAX_CONTRACT_IDENTIFIER_LENGTH)
    revision: int = Field(ge=1)
    viewer_count: int = Field(ge=1, le=MAX_VIEWER_COUNT)
    ambience: str = Field(min_length=1, max_length=64)
    base_activity: ActivityBand
    burst_limit: ActivityBand
    persona_ids: list[str] = Field(min_length=1, max_length=MAX_PERSONA_COUNT)
    persona_weights: dict[str, StrictInt] = Field(
        min_length=1,
        max_length=MAX_PERSONA_COUNT,
    )
    persona_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("mode_id", "namespace_id", "ambience")
    @classmethod
    def normalize_trimmed_string(cls, value: str) -> str:
        normalized = _normalize_string(value).strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized

    @field_validator("persona_ids")
    @classmethod
    def validate_persona_ids(cls, value: list[str]) -> list[str]:
        normalized = [_normalize_string(persona_id).strip() for persona_id in value]
        if any(not persona_id for persona_id in normalized):
            raise ValueError("persona_ids cannot contain blank values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("persona_ids cannot contain duplicates")
        return normalized

    @field_validator("persona_weights")
    @classmethod
    def validate_persona_weights(cls, value: dict[str, int]) -> dict[str, int]:
        normalized = {
            _normalize_string(persona_id).strip(): weight
            for persona_id, weight in value.items()
        }
        if any(not persona_id for persona_id in normalized):
            raise ValueError("persona_weights cannot contain blank IDs")
        if any(isinstance(weight, bool) or weight < 1 for weight in normalized.values()):
            raise ValueError("persona weights must be positive integers")
        return normalized

    @field_validator("persona_overrides")
    @classmethod
    def normalize_overrides(
        cls,
        value: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        return {
            _normalize_string(persona_id).strip(): _normalize_json(override)
            for persona_id, override in value.items()
        }

    @model_validator(mode="after")
    def validate_roster_graph(self) -> "AudienceModeConfiguration":
        roster = set(self.persona_ids)
        if set(self.persona_weights) != roster:
            raise ValueError("persona_weights must contain exactly the persona roster")
        if not set(self.persona_overrides) <= roster:
            raise ValueError("persona_overrides cannot reference personas outside the roster")
        return self


class SessionStartRequest(ContractModel):
    client_start_request_id: str = Field(
        min_length=1,
        max_length=MAX_CONTRACT_IDENTIFIER_LENGTH,
    )
    audience_contract_version: Literal[1] = AUDIENCE_CONTRACT_VERSION
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: AudienceModeConfiguration
    personas: list[PersonaTemplate] = Field(min_length=1, max_length=MAX_PERSONA_COUNT)

    @field_validator("client_start_request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = _normalize_string(value).strip()
        if not normalized:
            raise ValueError("client_start_request_id cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_persona_graph_and_hash(self) -> "SessionStartRequest":
        persona_ids = [persona.persona_id for persona in self.personas]
        if len(set(persona_ids)) != len(persona_ids):
            raise ValueError("personas cannot contain duplicate persona_id values")
        if set(persona_ids) != set(self.mode.persona_ids):
            raise ValueError("personas must contain exactly the active mode roster")
        if any(not persona.enabled for persona in self.personas):
            raise ValueError("disabled personas cannot be part of the active mode roster")
        computed_hash = hash_audience_configuration(
            audience_contract_version=self.audience_contract_version,
            mode=self.mode,
            personas=self.personas,
        )
        if self.config_hash != computed_hash:
            raise ValueError("config_hash does not match normalized audience configuration")
        return self

    def normalized_audience_configuration(self) -> dict[str, Any]:
        return normalize_audience_configuration(
            audience_contract_version=self.audience_contract_version,
            mode=self.mode,
            personas=self.personas,
        )


class PersonaAllocation(ContractModel):
    persona_id: str
    viewer_count: int = Field(ge=0, le=MAX_VIEWER_COUNT)


class SessionStartResponse(ContractModel):
    session_id: str
    state: SessionState
    started_at_ms: int | None
    updated_at_ms: int
    revision: int
    snapshot_hash: str
    mode_id: str
    mode_namespace_id: str
    mode_revision: int
    viewer_count: int
    allocations: list[PersonaAllocation]

    @classmethod
    def from_domain(
        cls,
        status: SessionStatus,
        request: SessionStartRequest,
    ) -> "SessionStartResponse":
        if status.session_id is None:
            raise ValueError("started Session response requires a session_id")
        return cls(
            session_id=status.session_id,
            state=status.state,
            started_at_ms=status.started_at_ms,
            updated_at_ms=status.updated_at_ms,
            revision=status.revision,
            snapshot_hash=request.config_hash,
            mode_id=request.mode.mode_id,
            mode_namespace_id=request.mode.namespace_id,
            mode_revision=request.mode.revision,
            viewer_count=request.mode.viewer_count,
            allocations=_allocate_viewers(request.mode),
        )


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


def normalize_audience_configuration(
    *,
    audience_contract_version: int,
    mode: AudienceModeConfiguration,
    personas: list[PersonaTemplate],
) -> dict[str, Any]:
    return {
        "audience_contract_version": audience_contract_version,
        "mode": mode.model_dump(mode="json"),
        "personas": [
            persona.model_dump(mode="json")
            for persona in sorted(personas, key=lambda item: item.persona_id)
        ],
    }


def canonical_audience_json(configuration: dict[str, Any]) -> bytes:
    return json.dumps(
        configuration,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def hash_audience_configuration(
    *,
    audience_contract_version: int,
    mode: AudienceModeConfiguration,
    personas: list[PersonaTemplate],
) -> str:
    normalized = normalize_audience_configuration(
        audience_contract_version=audience_contract_version,
        mode=mode,
        personas=personas,
    )
    return hashlib.sha256(canonical_audience_json(normalized)).hexdigest()


def _normalize_string(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _normalize_json(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        return {
            _normalize_string(str(key)): _normalize_json(item)
            for key, item in value.items()
        }
    if value is None or isinstance(value, (bool, int)):
        return value
    raise ValueError("audience configuration accepts JSON strings, integers, booleans, and null")


def _allocate_viewers(mode: AudienceModeConfiguration) -> list[PersonaAllocation]:
    total_weight = sum(mode.persona_weights.values())
    quotas = {
        persona_id: mode.viewer_count * mode.persona_weights[persona_id] / total_weight
        for persona_id in mode.persona_ids
    }
    counts = {persona_id: int(quota) for persona_id, quota in quotas.items()}
    remaining = mode.viewer_count - sum(counts.values())
    roster_order = {persona_id: index for index, persona_id in enumerate(mode.persona_ids)}
    ranked = sorted(
        mode.persona_ids,
        key=lambda persona_id: (
            -(quotas[persona_id] - counts[persona_id]),
            roster_order[persona_id],
            persona_id,
        ),
    )
    for persona_id in ranked[:remaining]:
        counts[persona_id] += 1
    return [
        PersonaAllocation(persona_id=persona_id, viewer_count=counts[persona_id])
        for persona_id in mode.persona_ids
    ]
