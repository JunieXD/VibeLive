from advx_backend.application.viewer_barrage_pipeline import (
    ViewerBarragePipeline,
    ViewerBarrageRejection,
)
from advx_backend.contracts.viewer_runtime import (
    EvidenceRef,
    EvidenceSource,
    ViewerAction,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
)
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation_wave import ViewerVisualInputMode
from advx_backend.domain.viewer import ViewerInstanceVariant, ViewerPrivateState


class MutableClock:
    def __init__(self, now_ms: int = 100) -> None:
        self.value = now_ms

    def now_ms(self) -> int:
        return self.value


class FixedIdGenerator:
    def new_id(self) -> str:
        return "barrage-1"


def request() -> ViewerGenerationRequest:
    return ViewerGenerationRequest(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="wave-1",
        generation_request_id="request-1",
        viewer_instance_id="viewer-1",
        viewer_sequence=1,
        persona_revision=1,
        instance_variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="gameplay",
            silence_tendency=0.2,
        ),
        mode_context={
            "_viewer_persona_id": "persona-1",
            "_viewer_display_name": "Viewer One",
        },
        visual_input_mode=ViewerVisualInputMode.SHARED_SUMMARY,
        shared_visual_summary="The game is visible.",
        input_event_ids=["event-1"],
        viewer_private_state=ViewerPrivateState(),
        room_memory_slice=RoomMemorySlice(
            room_id="room-1",
            memory_revision=0,
        ),
        deadline_at_ms=1_000,
    )


def response(*, event_id: str = "event-1") -> ViewerGenerationResponse:
    return ViewerGenerationResponse(
        generation_request_id="request-1",
        viewer_instance_id="viewer-1",
        viewer_sequence=1,
        action=ViewerAction.BARRAGE,
        text="nice play",
        reaction_type="reply",
        evidence_refs=[
            EvidenceRef(source=EvidenceSource.EVENT, event_id=event_id),
        ],
    )


def test_pipeline_builds_trusted_event_from_matching_response() -> None:
    pipeline = ViewerBarragePipeline(
        clock=MutableClock(),
        id_generator=FixedIdGenerator(),
    )

    result = pipeline.validate(request=request(), response=response())

    assert result.accepted is True
    assert result.event is not None
    assert result.event.persona_id == "persona-1"
    assert result.event.display_name == "Viewer One"
    assert result.event.expires_at_ms == 1_000


def test_pipeline_rejects_unrequested_evidence_without_event() -> None:
    pipeline = ViewerBarragePipeline(
        clock=MutableClock(),
        id_generator=FixedIdGenerator(),
    )

    result = pipeline.validate(
        request=request(),
        response=response(event_id="not-requested"),
    )

    assert result.accepted is False
    assert result.event is None
    assert result.rejection_reason is ViewerBarrageRejection.EVIDENCE_NOT_IN_REQUEST
