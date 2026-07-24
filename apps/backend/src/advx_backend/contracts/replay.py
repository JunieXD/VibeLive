import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from advx_backend.contracts.debug import ViewerRequestTrace
from advx_backend.contracts.protocol import (
    AUDIENCE_CONTRACT_VERSION,
    PROTOCOL_VERSION,
    REPLAY_SCHEMA_VERSION,
)
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec


class ReplayContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReplayMode(StrEnum):
    RECORDED = "recorded"
    LIVE = "live"


class ReplayEvent(ReplayContractModel):
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=128)
    occurred_at_ms: int = Field(ge=0)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class RecordedProviderOutput(ReplayContractModel):
    generation_request_id: str = Field(min_length=1, max_length=128)
    provider_role: Literal[
        "viewer",
        "memory",
        "visual_summary",
        "history_summary",
        "asr",
    ]
    output: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_role_output(self) -> "RecordedProviderOutput":
        allowed = {
            "viewer": {
                "action",
                "text",
                "reaction_type",
                "evidence_event_ids",
                "evidence_frame_indexes",
            },
            "memory": {"candidates"},
            "visual_summary": {"summary"},
            "history_summary": {"summary"},
            "asr": {"text", "final", "started_at_ms", "ended_at_ms"},
        }[self.provider_role]
        if not self.output or not set(self.output).issubset(allowed):
            raise ValueError("recorded Provider output is not role-whitelisted")
        return self


class ReplayBundle(ReplayContractModel):
    replay_schema_version: Literal[1] = REPLAY_SCHEMA_VERSION
    protocol_version: Literal[3] = PROTOCOL_VERSION
    audience_contract_version: Literal[2] = AUDIENCE_CONTRACT_VERSION
    bundle_id: str = Field(min_length=1, max_length=128)
    created_at_ms: int = Field(ge=0)
    seed: int
    virtual_clock_start_ms: int = Field(ge=0)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_runtime_spec: CanonicalRuntimeSpec
    input_refs: list[str] = Field(default_factory=list, max_length=1024)
    events: list[ReplayEvent] = Field(min_length=1)
    recorded_provider_outputs: list[RecordedProviderOutput] = Field(min_length=1)
    recorded_outputs_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    traces: list[ViewerRequestTrace] = Field(default_factory=list)
    redacted: Literal[True] = True

    @model_validator(mode="after")
    def validate_bundle(self) -> "ReplayBundle":
        if self.config_hash != self.canonical_runtime_spec.config_hash():
            raise ValueError("config_hash does not match canonical_runtime_spec")
        sequences = [event.sequence for event in self.events]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("Replay event sequences must be contiguous and start at one")
        timestamps = [event.occurred_at_ms for event in self.events]
        if timestamps != sorted(timestamps):
            raise ValueError("Replay event timestamps must be nondecreasing")
        output_keys = [
            (output.provider_role, output.generation_request_id)
            for output in self.recorded_provider_outputs
        ]
        if len(output_keys) != len(set(output_keys)):
            raise ValueError("recorded Provider output identities must be unique")
        digest = self.compute_recorded_outputs_digest()
        if self.recorded_outputs_digest is None:
            object.__setattr__(self, "recorded_outputs_digest", digest)
        elif self.recorded_outputs_digest != digest:
            raise ValueError("recorded Provider outputs do not match their digest")
        self.assert_recorded_output_correlations()
        return self

    def compute_recorded_outputs_digest(self) -> str:
        encoded = json.dumps(
            [
                output.model_dump(mode="json")
                for output in self.recorded_provider_outputs
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def assert_recorded_outputs_integrity(self) -> None:
        if self.recorded_outputs_digest != self.compute_recorded_outputs_digest():
            raise ValueError("recorded Provider outputs do not match their digest")

    def assert_recorded_output_correlations(self) -> None:
        referenced: list[tuple[str, str]] = []
        for event in self.events:
            role = event.event_type.partition(".")[0]
            raw_ids = event.payload.get("generation_request_ids")
            raw_id = event.payload.get("generation_request_id")
            if isinstance(raw_ids, list):
                referenced.extend(
                    (role, item) for item in raw_ids if isinstance(item, str)
                )
            elif isinstance(raw_id, str):
                referenced.append((role, raw_id))
        if len(referenced) != len(set(referenced)):
            raise ValueError("recorded output identity is referenced more than once")
        output_keys = [
            (output.provider_role, output.generation_request_id)
            for output in self.recorded_provider_outputs
        ]
        if set(referenced) != set(output_keys):
            raise ValueError("recorded output identities do not match replay events")


class ReplayRequest(ReplayContractModel):
    mode: ReplayMode = ReplayMode.RECORDED
    bundle: ReplayBundle
    allow_external_provider_calls: bool = False

    @model_validator(mode="after")
    def validate_live_opt_in(self) -> "ReplayRequest":
        if self.mode is ReplayMode.LIVE and not self.allow_external_provider_calls:
            raise ValueError("live replay requires explicit external Provider opt-in")
        if self.mode is ReplayMode.RECORDED and self.allow_external_provider_calls:
            raise ValueError("recorded replay cannot allow external Provider calls")
        return self


class RecordedOutputConsumption(ReplayContractModel):
    provider_role: Literal[
        "viewer",
        "memory",
        "visual_summary",
        "history_summary",
        "asr",
    ]
    generation_request_id: str = Field(min_length=1, max_length=128)
    call_index: int = Field(ge=1)
    runtime_request_id: str | None = Field(default=None, min_length=1, max_length=128)


class RecordedReplayEvidence(ReplayContractModel):
    decisions: list[dict[str, JsonValue]]
    selected_viewer_ids: list[str]
    barrages: list[dict[str, JsonValue]]
    memories: list[dict[str, JsonValue]]
    traces: list[dict[str, JsonValue]]
    consumed_provider_roles: list[
        Literal[
            "viewer",
            "memory",
            "visual_summary",
            "history_summary",
            "asr",
        ]
    ]
    consumed_provider_outputs: list[RecordedOutputConsumption]
    external_transport_call_count: Literal[0] = 0


class ReplayResult(ReplayContractModel):
    bundle_id: str
    mode: ReplayMode
    deterministic_proof: bool
    credentialed_provider_proof: bool
    event_count: int = Field(ge=0)
    trace_count: int = Field(ge=0)
    completed_at_ms: int = Field(ge=0)
    replay_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    recorded_evidence: RecordedReplayEvidence | None = None
    provider_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    external_transport_call_count: int = Field(default=0, ge=0)


class LiveReplayEvidence(ReplayContractModel):
    provider_profile_id: str = Field(min_length=1, max_length=128)
    provider_kind: Literal["openai_compatible"]
    credentialed: Literal[True]
    external_transport_verified: Literal[True]
    external_transport_call_count: int = Field(ge=1)
    fake_fallback_used: Literal[False] = False
