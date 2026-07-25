import hashlib
import json
from collections.abc import Sequence
from typing import Protocol

from advx_backend.contracts.debug import (
    MemoryReferenceTrace,
    ObservationWaveStatus,
    ObservationWaveTrace,
    PromptManifest,
    ProviderTrace,
    SideEffectTrace,
    TraceResponseStatus,
    ValidationTrace,
    ViewerRequestTrace,
)
from advx_backend.contracts.viewer_runtime import ViewerGenerationRequest
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.observation_wave import ObservationWave
from advx_backend.domain.viewer import ViewerInstance


class ViewerTraceSink(Protocol):
    def record(self, trace: ViewerRequestTrace | ObservationWaveTrace) -> None: ...


def build_viewer_request_trace(
    *,
    request: ViewerGenerationRequest,
    viewer: ViewerInstance,
    wave: ObservationWave,
    decision: CrowdDecision,
    available_viewer_ids: Sequence[str],
    runtime: object,
    queued_at_ms: int,
    dispatched_at_ms: int | None,
    completed_at_ms: int | None,
    response_status: TraceResponseStatus,
    retry_count: int,
    accepted: bool,
    validation_codes: Sequence[str] = (),
    stale_or_cancel_reason: str | None = None,
    published_barrage_id: str | None = None,
    published_barrage_ids: Sequence[str] = (),
) -> ViewerRequestTrace:
    spec = getattr(runtime, "canonical_runtime_spec", runtime)
    config_hash = _config_hash(spec)
    provider = getattr(spec, "provider", None)
    viewer_model = getattr(provider, "viewer_model", "unknown-viewer-model")
    frame_hashes = (
        []
        if wave.frame_bundle is None
        else [frame.content_hash for frame in wave.frame_bundle.frames]
    )
    private_event_ids = list(
        dict.fromkeys(
            [
                *request.viewer_private_state.published_event_ids,
                *request.viewer_private_state.direct_interaction_event_ids,
            ]
        )
    )
    published_ids = list(dict.fromkeys(published_barrage_ids))
    if published_barrage_id is not None and published_barrage_id not in published_ids:
        published_ids.insert(0, published_barrage_id)
    return ViewerRequestTrace(
        trace_id=request.generation_request_id,
        room_id=request.room_id,
        session_id=request.session_id,
        audience_epoch=request.audience_epoch,
        config_hash=config_hash,
        observation_id=request.observation_id,
        decision=decision,
        viewer_instance_id=request.viewer_instance_id,
        viewer_sequence=request.viewer_sequence,
        persona_revision=request.persona_revision,
        instance_variant=request.instance_variant,
        public_context_event_ids=request.public_context_event_ids,
        private_state_event_ids=private_event_ids,
        memory=_memory_reference(request.room_memory_slice),
        frame_hashes=frame_hashes,
        prompt_manifest=PromptManifest(
            template_id="viewer-generation-v1",
            template_revision=1,
            input_hash=_canonical_request_metadata_hash(request, frame_hashes),
            sections=_manifest_sections(request),
        ),
        provider=ProviderTrace(
            provider_role="viewer",
            model_id=str(viewer_model),
            queued_at_ms=queued_at_ms,
            dispatched_at_ms=dispatched_at_ms,
            completed_at_ms=completed_at_ms,
        ),
        response_status=response_status,
        validation=ValidationTrace(accepted=accepted, codes=list(validation_codes)),
        retry_count=retry_count,
        stale_or_cancel_reason=stale_or_cancel_reason,
        side_effects=SideEffectTrace(
            published_barrage_id=(published_ids[0] if published_ids else None),
            published_barrage_ids=published_ids,
        ),
    )


def build_observation_wave_trace(
    *,
    wave: ObservationWave,
    runtime: object | None,
    status: ObservationWaveStatus,
    decision: CrowdDecision | None = None,
    failure_reason: str | None = None,
) -> ObservationWaveTrace:
    spec = getattr(runtime, "canonical_runtime_spec", runtime)
    memory_slice = getattr(runtime, "room_memory_slice", None)
    frame_hashes = (
        []
        if wave.frame_bundle is None
        else [frame.content_hash for frame in wave.frame_bundle.frames]
    )
    return ObservationWaveTrace(
        trace_id=f"wave-{wave.observation_id}",
        room_id=wave.room_id,
        session_id=wave.session_id,
        audience_epoch=wave.audience_epoch,
        config_hash=_config_hash(spec),
        observation_id=wave.observation_id,
        created_at_ms=wave.created_at_ms,
        deadline_at_ms=wave.deadline_at_ms,
        triggers=wave.triggers,
        event_ids=wave.event_ids,
        trigger_event_ids=wave.trigger_event_ids,
        frame_hashes=frame_hashes,
        memory=_memory_reference(
            memory_slice,
            room_id=wave.room_id,
        ),
        status=status,
        selected_viewer_ids=(
            [] if decision is None else decision.selected_viewer_ids
        ),
        decision_id=None if decision is None else decision.decision_id,
        decision_source=None if decision is None else decision.decision_source,
        reason_codes=[] if decision is None else decision.reason_codes,
        failure_reason=failure_reason,
    )


def _memory_reference(
    memory_slice: object,
    *,
    room_id: str | None = None,
) -> MemoryReferenceTrace:
    resolved_room_id = getattr(memory_slice, "room_id", room_id)
    if not isinstance(resolved_room_id, str) or not resolved_room_id:
        raise ValueError("memory trace requires a room id")
    revision = getattr(memory_slice, "memory_revision", 0)
    memory_ids = getattr(memory_slice, "memory_ids", ())
    return MemoryReferenceTrace(
        room_id=resolved_room_id,
        memory_revision=revision if isinstance(revision, int) else 0,
        memory_ids=list(memory_ids) if isinstance(memory_ids, (list, tuple)) else [],
    )


def _canonical_request_metadata_hash(
    request: ViewerGenerationRequest,
    frame_hashes: Sequence[str],
) -> str:
    metadata = {
        "room_id": request.room_id,
        "session_id": request.session_id,
        "audience_epoch": request.audience_epoch,
        "observation_id": request.observation_id,
        "generation_request_id": request.generation_request_id,
        "viewer_instance_id": request.viewer_instance_id,
        "viewer_sequence": request.viewer_sequence,
        "persona_revision": request.persona_revision,
        "instance_variant": request.instance_variant.model_dump(mode="json"),
        "mode_id": request.mode_context.get("mode_id"),
        "visual_input_mode": request.visual_input_mode.value,
        "frame_hashes": list(frame_hashes),
        "input_event_ids": request.input_event_ids,
        "public_context_event_ids": request.public_context_event_ids,
        "private_state_revision": request.viewer_private_state.revision,
        "private_state_event_ids": [
            *request.viewer_private_state.published_event_ids,
            *request.viewer_private_state.direct_interaction_event_ids,
        ],
        "memory_revision": request.room_memory_slice.memory_revision,
        "memory_ids": request.room_memory_slice.memory_ids,
        "deadline_at_ms": request.deadline_at_ms,
    }
    canonical = json.dumps(
        metadata,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _manifest_sections(request: ViewerGenerationRequest) -> list[str]:
    sections = ["viewer-identity", "mode-metadata", "public-context", "private-state"]
    if request.room_memory_slice.memory_ids:
        sections.append("room-memory-refs")
    if request.frame_bundle is not None:
        sections.append("frame-hashes")
    elif request.shared_visual_summary is not None:
        sections.append("shared-visual-summary-present")
    return sections


def _config_hash(spec: object) -> str:
    value = getattr(spec, "config_hash", None)
    if callable(value):
        resolved = value()
        if isinstance(resolved, str):
            return resolved
    if isinstance(value, str):
        return value
    return "0" * 64
