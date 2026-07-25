import hashlib
import json
import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from advx_backend.contracts.configuration import RuntimeModelProviderCandidate
from advx_backend.contracts.protocol import AUDIENCE_CONTRACT_VERSION, PROTOCOL_VERSION
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleSettings,
    ViewerVisualInputMode,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstance, ViewerInstanceVariant, ViewerPrivateState


class RuntimeContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Room(RuntimeContractModel):
    room_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=1, ge=1)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "Room":
        if self.updated_at_ms < self.created_at_ms:
            raise ValueError("updated_at_ms must not precede created_at_ms")
        return self


class ProviderRuntimeSpec(RuntimeContractModel):
    provider_profile_id: str = Field(min_length=1, max_length=128)
    viewer_model: str = Field(min_length=1, max_length=256)
    memory_model: str = Field(min_length=1, max_length=256)
    visual_summary_model: str = Field(min_length=1, max_length=256)


class BarrageGenerationMode(StrEnum):
    PER_VIEWER = "per_viewer"
    WINDOW_BATCH = "window_batch"


class RuntimeSettings(RuntimeContractModel):
    barrage_generation_mode: BarrageGenerationMode = BarrageGenerationMode.PER_VIEWER
    frame_bundle: FrameBundleSettings = Field(default_factory=FrameBundleSettings)
    viewer_visual_input_mode: ViewerVisualInputMode = ViewerVisualInputMode.DIRECT_FRAMES
    max_in_flight_viewer_requests: int = Field(default=6, ge=1, le=32)
    viewer_request_ttl_ms: int = Field(default=30_000, ge=1)
    viewer_queue_capacity: int = Field(default=64, ge=1, le=65_536)
    observation_merge_window_ms: int = Field(default=1_000, ge=0)
    public_context_window_ms: int = Field(default=60_000, ge=1)
    public_context_max_events: int = Field(default=48, ge=1, le=128)
    replyable_event_window_ms: int = Field(default=30_000, ge=1)
    max_replyable_events: int = Field(default=8, ge=0, le=32)
    viewer_user_speaker_budget: int = Field(default=6, ge=0, le=32)
    viewer_screen_speaker_budget: int = Field(default=4, ge=0, le=32)
    viewer_ambient_speaker_budget: int = Field(default=2, ge=0, le=32)
    max_direct_frame_age_ms: int = Field(default=30_000, ge=1)
    screen_change_threshold: float = Field(default=0.2, ge=0, le=1)
    screen_change_cooldown_ms: int = Field(default=10_000, ge=0)
    ambient_tick_cooldown_ms: int = Field(default=30_000, ge=1)
    max_consecutive_ambient_waves: int = Field(default=1, ge=0, le=32)
    window_batch_interval_ms: int = Field(default=5_000, ge=1)
    window_batch_context_window_ms: int = Field(default=30_000, ge=1)
    window_batch_max_frames: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def validate_window_batch_preset(self) -> "RuntimeSettings":
        if self.barrage_generation_mode is not BarrageGenerationMode.WINDOW_BATCH:
            return self
        if (
            self.window_batch_interval_ms != 5_000
            or self.window_batch_context_window_ms != 30_000
            or self.window_batch_max_frames != 5
        ):
            raise ValueError(
                "window_batch requires a 5000 ms interval, 30000 ms context, and 5 frames"
            )
        return self


class ViewerRuntimeTelemetry(RuntimeContractModel):
    selected: int = Field(default=0, ge=0)
    queued: int = Field(default=0, ge=0)
    dispatched: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    silence: int = Field(default=0, ge=0)
    published: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    expired: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    stale: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    superseded: int = Field(default=0, ge=0)
    retry: int = Field(default=0, ge=0)


class CanonicalRuntimeSpec(RuntimeContractModel):
    protocol_version: Literal[3] = PROTOCOL_VERSION
    audience_contract_version: Literal[2] = AUDIENCE_CONTRACT_VERSION
    config_revision: int = Field(ge=1)
    room: Room
    active_mode_id: str = Field(min_length=1, max_length=128)
    personas: list[PersonaTemplate] = Field(min_length=1)
    modes: list[ModeDefinition] = Field(min_length=1)
    provider: ProviderRuntimeSpec
    settings: RuntimeSettings = Field(default_factory=RuntimeSettings)

    @model_validator(mode="after")
    def validate_references(self) -> "CanonicalRuntimeSpec":
        persona_by_id = {persona.persona_id: persona for persona in self.personas}
        if len(persona_by_id) != len(self.personas):
            raise ValueError("persona IDs must be unique")
        mode_by_id = {mode.mode_id: mode for mode in self.modes}
        if len(mode_by_id) != len(self.modes):
            raise ValueError("mode IDs must be unique")
        if self.active_mode_id not in mode_by_id:
            raise ValueError("active_mode_id must reference a configured Mode")
        for mode in self.modes:
            unknown = set(mode.persona_ids) - set(persona_by_id)
            if unknown:
                raise ValueError(f"Mode {mode.mode_id} references unknown Personas")
            if not any(
                persona_by_id[persona_id].enabled
                and mode.persona_weights[persona_id] > 0
                for persona_id in mode.persona_ids
            ):
                raise ValueError(
                    f"Mode {mode.mode_id} requires an enabled Persona with positive weight"
                )
        return self

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json", exclude_none=True))

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return f"[{','.join(_canonical_json(item) for item in value)}]"
    if isinstance(value, dict):
        items = (
            f"{json.dumps(key, ensure_ascii=False)}:{_canonical_json(value[key])}"
            for key in sorted(value, key=_utf16_sort_key)
        )
        return f"{{{','.join(items)}}}"
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def _utf16_sort_key(value: str) -> bytes:
    """Match ECMAScript's lexicographic comparison of UTF-16 code units."""
    return value.encode("utf-16-be", errors="surrogatepass")


def _canonical_float(value: float) -> str:
    """Render a finite binary64 value using ECMAScript's JSON number form."""
    if not math.isfinite(value):
        raise ValueError("canonical JSON numbers must be finite")
    if value == 0:
        return "0"

    sign = "-" if value < 0 else ""
    representation = repr(abs(value)).lower()
    coefficient, exponent_text = (
        representation.split("e", maxsplit=1)
        if "e" in representation
        else (representation, "0")
    )
    exponent = int(exponent_text)
    if "." in coefficient:
        whole, fraction = coefficient.split(".", maxsplit=1)
        digits = f"{whole}{fraction}"
        exponent -= len(fraction)
    else:
        digits = coefficient

    digits = digits.lstrip("0") or "0"
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]
        exponent += 1

    decimal_point = len(digits) + exponent
    if 0 < decimal_point <= 21:
        if decimal_point >= len(digits):
            rendered = f"{digits}{'0' * (decimal_point - len(digits))}"
        else:
            rendered = f"{digits[:decimal_point]}.{digits[decimal_point:]}"
    elif -6 < decimal_point <= 0:
        rendered = f"0.{'0' * -decimal_point}{digits}"
    else:
        significand = digits[0]
        if len(digits) > 1:
            significand = f"{significand}.{digits[1:]}"
        scientific_exponent = decimal_point - 1
        exponent_sign = "+" if scientific_exponent >= 0 else ""
        rendered = f"{significand}e{exponent_sign}{scientific_exponent}"
    return f"{sign}{rendered}"


class RuntimeDiffSummary(RuntimeContractModel):
    changed_paths: list[str] = Field(default_factory=list, max_length=1024)
    added_viewer_ids: list[str] = Field(default_factory=list, max_length=32)
    retained_viewer_ids: list[str] = Field(default_factory=list, max_length=32)
    reset_viewer_ids: list[str] = Field(default_factory=list, max_length=32)
    removed_viewer_ids: list[str] = Field(default_factory=list, max_length=32)


class RuntimeApplyRequest(RuntimeContractModel):
    apply_id: str = Field(min_length=1, max_length=128)
    base_revision: int = Field(ge=0)
    audience_contract_version: Literal[2] = AUDIENCE_CONTRACT_VERSION
    canonical_runtime_spec: CanonicalRuntimeSpec
    client_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_candidate: RuntimeModelProviderCandidate | None = Field(
        default=None,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_config_hash(self) -> "RuntimeApplyRequest":
        if self.client_config_hash != self.canonical_runtime_spec.config_hash():
            raise ValueError("client_config_hash does not match canonical_runtime_spec")
        return self


class RuntimeApplyResponse(RuntimeContractModel):
    apply_id: str
    room_id: str
    session_id: str
    audience_epoch: int = Field(ge=1)
    config_revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_at_ms: int = Field(ge=0)
    diff: RuntimeDiffSummary


class RuntimeQueryResponse(RuntimeContractModel):
    room_id: str
    session_id: str
    audience_epoch: int = Field(ge=1)
    config_revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_runtime_spec: CanonicalRuntimeSpec
    viewers: list[ViewerInstance] = Field(max_length=32)


class RuntimeRollbackRequest(RuntimeContractModel):
    apply_id: str = Field(min_length=1, max_length=128)
    base_revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)
    audience_contract_version: Literal[2] = AUDIENCE_CONTRACT_VERSION
    provider_candidate: RuntimeModelProviderCandidate | None = Field(
        default=None,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_target_revision(self) -> "RuntimeRollbackRequest":
        if self.target_revision >= self.base_revision:
            raise ValueError("target_revision must precede base_revision")
        return self


class EvidenceSource(StrEnum):
    EVENT = "event"
    FRAME = "frame"


class EvidenceRef(RuntimeContractModel):
    source: EvidenceSource
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    frame_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_reference(self) -> "EvidenceRef":
        if self.source is EvidenceSource.EVENT:
            if self.event_id is None or self.frame_index is not None:
                raise ValueError("event evidence requires only event_id")
        elif self.frame_index is None or self.event_id is not None:
            raise ValueError("frame evidence requires only frame_index")
        return self


class ViewerPublicEvent(RuntimeContractModel):
    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    source_type: str = Field(min_length=1, max_length=64)
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=4_000)
    viewer_instance_id: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    target_viewer_id: str | None = Field(default=None, min_length=1, max_length=128)
    occurred_at_ms: int = Field(ge=0)


class ViewerGenerationRequest(RuntimeContractModel):
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=128)
    generation_request_id: str = Field(min_length=1, max_length=128)
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    viewer_sequence: int = Field(ge=1)
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=64)
    persona: PersonaTemplate
    persona_revision: int = Field(ge=1)
    presence_revision: int = Field(ge=1)
    moderation_revision: int = Field(ge=1)
    behavior_revision: int = Field(ge=1)
    scene_assessment: SceneAssessment
    active_viewer_ids: list[str] = Field(default_factory=list, max_length=128)
    instance_variant: ViewerInstanceVariant
    mode_context: dict[str, JsonValue]
    visual_input_mode: ViewerVisualInputMode
    frame_bundle: FrameBundle | None = None
    shared_visual_summary: str | None = Field(default=None, max_length=8_000)
    input_event_ids: list[str] = Field(default_factory=list, max_length=128)
    public_context_event_ids: list[str] = Field(default_factory=list, max_length=4_096)
    public_context: list[ViewerPublicEvent] = Field(default_factory=list, max_length=4_096)
    reply_context_event_ids: list[str] = Field(default_factory=list, max_length=32)
    reply_context: list[ViewerPublicEvent] = Field(default_factory=list, max_length=32)
    conversation_history_summary: str | None = Field(default=None, max_length=6_000)
    viewer_private_state: ViewerPrivateState
    room_memory_slice: RoomMemorySlice
    deadline_at_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_visual_input(self) -> "ViewerGenerationRequest":
        if self.visual_input_mode is ViewerVisualInputMode.DIRECT_FRAMES:
            if self.frame_bundle is None or self.shared_visual_summary is not None:
                raise ValueError("direct_frames requires only frame_bundle")
        elif self.visual_input_mode is ViewerVisualInputMode.SHARED_SUMMARY and (
            self.shared_visual_summary is None or self.frame_bundle is not None
        ):
            raise ValueError("shared_summary requires only shared_visual_summary")
        elif self.visual_input_mode is ViewerVisualInputMode.TEXT_ONLY and (
            self.frame_bundle is not None or self.shared_visual_summary is not None
        ):
            raise ValueError("text_only cannot include visual input")
        return self


class ViewerReactionIntent(StrEnum):
    REACT_TO_HOST = "react_to_host"
    REACT_TO_SCENE = "react_to_scene"
    REPLY_TO_VIEWER = "reply_to_viewer"
    ASK_QUESTION = "ask_question"
    AGREE = "agree"
    DISAGREE = "disagree"
    ENCOURAGE = "encourage"
    JOKE = "joke"
    CONTINUE_THREAD = "continue_thread"
    ROOM_META = "room_meta"
    SILENCE = "silence"


class ViewerTargetKind(StrEnum):
    HOST = "host"
    SCENE = "scene"
    ROOM = "room"
    VIEWER = "viewer"
    EVENT = "event"


class ViewerReactionTarget(RuntimeContractModel):
    kind: ViewerTargetKind
    viewer_instance_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_target(self) -> "ViewerReactionTarget":
        if self.kind is ViewerTargetKind.VIEWER and self.viewer_instance_id is None:
            raise ValueError("viewer target requires viewer_instance_id")
        if self.kind is ViewerTargetKind.EVENT and self.event_id is None:
            raise ValueError("event target requires event_id")
        if self.kind is not ViewerTargetKind.VIEWER and self.viewer_instance_id is not None:
            raise ValueError("viewer_instance_id requires viewer target")
        if self.kind is not ViewerTargetKind.EVENT and self.event_id is not None:
            raise ValueError("event_id requires event target")
        return self


class ViewerAction(StrEnum):
    BARRAGE = "barrage"
    SILENCE = "silence"


class ViewerGenerationResponse(RuntimeContractModel):
    generation_request_id: str = Field(min_length=1, max_length=128)
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    viewer_sequence: int = Field(ge=1)
    action: ViewerAction
    intent: ViewerReactionIntent = ViewerReactionIntent.REACT_TO_SCENE
    target: ViewerReactionTarget | None = None
    text: str | None = Field(default=None, min_length=1, max_length=4_000)
    reaction_type: str = Field(min_length=1, max_length=64)
    decision_reason: str | None = Field(default=None, min_length=1, max_length=160)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def validate_action(self) -> "ViewerGenerationResponse":
        if self.action is ViewerAction.BARRAGE and self.text is None:
            raise ValueError("barrage requires text")
        if self.action is ViewerAction.SILENCE and self.text is not None:
            raise ValueError("silence cannot include text")
        if self.action is ViewerAction.SILENCE and self.target is not None:
            raise ValueError("silence cannot include target")
        return self


class WindowBatchGenerationRequest(RuntimeContractModel):
    batch_generation_request_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=128)
    requests: list[ViewerGenerationRequest] = Field(min_length=1, max_length=32)
    deadline_at_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_requests(self) -> "WindowBatchGenerationRequest":
        viewer_ids: set[str] = set()
        generation_ids: set[str] = set()
        for request in self.requests:
            if (
                request.room_id != self.room_id
                or request.session_id != self.session_id
                or request.audience_epoch != self.audience_epoch
                or request.observation_id != self.observation_id
                or request.deadline_at_ms != self.deadline_at_ms
            ):
                raise ValueError("batch requests must share one frozen observation scope")
            if request.viewer_instance_id in viewer_ids:
                raise ValueError("batch viewer IDs must be unique")
            if request.generation_request_id in generation_ids:
                raise ValueError("batch generation request IDs must be unique")
            viewer_ids.add(request.viewer_instance_id)
            generation_ids.add(request.generation_request_id)
        return self


class WindowBatchGenerationResponse(RuntimeContractModel):
    batch_generation_request_id: str = Field(min_length=1, max_length=128)
    candidates: list[ViewerGenerationResponse] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_candidates(self) -> "WindowBatchGenerationResponse":
        viewer_ids = [candidate.viewer_instance_id for candidate in self.candidates]
        if len(viewer_ids) != len(set(viewer_ids)):
            raise ValueError("batch candidate viewer IDs must be unique")
        return self


class ViewerBarrageEvent(RuntimeContractModel):
    barrage_id: str = Field(min_length=1, max_length=128)
    room_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    audience_epoch: int = Field(ge=1)
    observation_id: str = Field(min_length=1, max_length=128)
    generation_request_id: str = Field(min_length=1, max_length=128)
    viewer_instance_id: str = Field(min_length=1, max_length=128)
    persona_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    viewer_sequence: int = Field(ge=1)
    reaction_type: str = Field(min_length=1, max_length=64)
    intent: ViewerReactionIntent
    target: ViewerReactionTarget | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=128)
    text: str = Field(min_length=1, max_length=160)
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_expiry(self) -> "ViewerBarrageEvent":
        if self.expires_at_ms <= self.created_at_ms:
            raise ValueError("expires_at_ms must be later than created_at_ms")
        return self
