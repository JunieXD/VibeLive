from dataclasses import dataclass
from enum import StrEnum

from advx_backend.application.ports.session import Clock, IdGenerator
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerBarrageEvent,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
)


class ViewerBarrageRejection(StrEnum):
    EXPIRED = "expired"
    REQUEST_MISMATCH = "request_mismatch"
    VIEWER_MISMATCH = "viewer_mismatch"
    SEQUENCE_MISMATCH = "sequence_mismatch"
    EVIDENCE_NOT_IN_REQUEST = "evidence_not_in_request"


@dataclass(frozen=True, slots=True)
class ViewerBarrageValidation:
    accepted: bool
    event: ViewerBarrageEvent | None = None
    rejection_reason: ViewerBarrageRejection | None = None


class ViewerBarragePipeline:
    """Validate one Viewer response before it can enter trusted room state."""

    def __init__(self, *, clock: Clock, id_generator: IdGenerator) -> None:
        self._clock = clock
        self._id_generator = id_generator

    def validate(
        self,
        *,
        request: ViewerGenerationRequest,
        response: ViewerGenerationResponse,
    ) -> ViewerBarrageValidation:
        now_ms = self._clock.now_ms()
        if now_ms >= request.deadline_at_ms:
            return self._reject(ViewerBarrageRejection.EXPIRED)
        if response.generation_request_id != request.generation_request_id:
            return self._reject(ViewerBarrageRejection.REQUEST_MISMATCH)
        if response.viewer_instance_id != request.viewer_instance_id:
            return self._reject(ViewerBarrageRejection.VIEWER_MISMATCH)
        if response.viewer_sequence != request.viewer_sequence:
            return self._reject(ViewerBarrageRejection.SEQUENCE_MISMATCH)
        if not self._evidence_is_allowed(request, response.evidence_refs):
            return self._reject(ViewerBarrageRejection.EVIDENCE_NOT_IN_REQUEST)
        if response.action is ViewerAction.SILENCE:
            return ViewerBarrageValidation(accepted=True)

        persona_id = request.mode_context.get(
            "_viewer_persona_id", request.viewer_instance_id
        )
        display_name = request.mode_context.get(
            "_viewer_display_name", request.viewer_instance_id
        )
        if not isinstance(persona_id, str) or not persona_id:
            persona_id = request.viewer_instance_id
        if not isinstance(display_name, str) or not display_name:
            display_name = request.viewer_instance_id
        return ViewerBarrageValidation(
            accepted=True,
            event=ViewerBarrageEvent(
                barrage_id=self._id_generator.new_id(),
                room_id=request.room_id,
                session_id=request.session_id,
                audience_epoch=request.audience_epoch,
                observation_id=request.observation_id,
                generation_request_id=request.generation_request_id,
                viewer_instance_id=request.viewer_instance_id,
                persona_id=persona_id,
                display_name=display_name,
                viewer_sequence=request.viewer_sequence,
                reaction_type=response.reaction_type,
                evidence_refs=response.evidence_refs,
                text=response.text or "",
                created_at_ms=now_ms,
                expires_at_ms=request.deadline_at_ms,
            ),
        )

    @staticmethod
    def _evidence_is_allowed(
        request: ViewerGenerationRequest,
        evidence_refs: list[EvidenceRef],
    ) -> bool:
        event_ids = set(request.input_event_ids) | set(request.public_context_event_ids)
        frame_count = len(request.frame_bundle.frames) if request.frame_bundle is not None else 0
        for evidence in evidence_refs:
            if evidence.source is EvidenceSource.EVENT:
                if evidence.event_id not in event_ids:
                    return False
            elif evidence.frame_index is None or evidence.frame_index >= frame_count:
                return False
        return True

    @staticmethod
    def _reject(reason: ViewerBarrageRejection) -> ViewerBarrageValidation:
        return ViewerBarrageValidation(accepted=False, rejection_reason=reason)
