import pytest

from advx_backend.application.ports.ingest import FrameInput
from advx_backend.application.visual_signature import (
    VISUAL_SIGNATURE_BYTES,
    encode_visual_signature,
    visual_signature_change_score,
)


def test_frame_input_decodes_a_canonical_visual_signature_from_mime_metadata() -> None:
    signature = bytes([0x12]) * VISUAL_SIGNATURE_BYTES
    encoded = encode_visual_signature(signature)

    frame = FrameInput(
        session_id="session",
        input_id="frame",
        captured_at_ms=1,
        mime_type=(
            "image/jpeg;advx-change-score=0.125;"
            f"advx-visual-signature={encoded}"
        ),
        body=b"frame",
    )

    assert frame.mime_type == "image/jpeg"
    assert frame.change_score == 0.125
    assert frame.visual_signature == signature


def test_frame_input_rejects_a_malformed_visual_signature() -> None:
    with pytest.raises(ValueError, match="visual signature"):
        FrameInput(
            session_id="session",
            input_id="frame",
            captured_at_ms=1,
            mime_type="image/jpeg;advx-visual-signature=not-a-signature",
            body=b"frame",
        )


def test_visual_signature_delta_uses_the_packed_grayscale_values() -> None:
    dark = bytes([0x00]) * VISUAL_SIGNATURE_BYTES
    light = bytes([0xFF]) * VISUAL_SIGNATURE_BYTES

    assert visual_signature_change_score(dark, dark) == 0
    assert visual_signature_change_score(dark, light) == 1
