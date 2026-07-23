import struct

import pytest
from pydantic import ValidationError

from advx_backend.contracts.binary import (
    BINARY_ENVELOPE_VERSION,
    BINARY_FIXED_HEADER_BYTES,
    MAX_AUDIO_BODY_BYTES,
    BinaryEnvelopeError,
    BinaryEnvelopeHeader,
    BinaryInputEnvelope,
    BinaryMediaType,
    decode_binary_envelope,
    encode_binary_envelope,
)


def create_envelope(
    *,
    media_type: BinaryMediaType = BinaryMediaType.AUDIO,
    format_value: str = "audio/pcm;rate=16000;channels=1;format=s16le",
    body: bytes = b"private-audio-body",
) -> BinaryInputEnvelope:
    return BinaryInputEnvelope(
        header=BinaryEnvelopeHeader(
            version=BINARY_ENVELOPE_VERSION,
            media_type=media_type,
            session_id="session-1",
            input_id="input-1",
            captured_at_ms=1_234,
            format=format_value,
            body_length=len(body),
        ),
        body=body,
    )


@pytest.mark.parametrize(
    ("media_type", "format_value", "body"),
    [
        (
            BinaryMediaType.AUDIO,
            "audio/pcm;rate=16000;channels=1;format=s16le",
            b"\x00\x01\x02",
        ),
        (BinaryMediaType.IMAGE, "image/webp", b"RIFFprivate-image-bytes"),
    ],
)
def test_binary_envelope_round_trips(
    media_type: BinaryMediaType,
    format_value: str,
    body: bytes,
) -> None:
    envelope = create_envelope(
        media_type=media_type,
        format_value=format_value,
        body=body,
    )

    encoded = encode_binary_envelope(envelope)
    decoded = decode_binary_envelope(encoded)

    assert len(encoded) == (
        BINARY_FIXED_HEADER_BYTES
        + len(envelope.header.session_id.encode())
        + len(envelope.header.input_id.encode())
        + len(envelope.header.format.encode())
        + len(body)
    )
    assert decoded.header == envelope.header
    assert decoded.body == body
    assert body.decode("latin1") not in repr(decoded)
    assert "body=" not in repr(decoded)


def test_binary_envelope_rejects_declared_length_mismatch() -> None:
    encoded = bytearray(encode_binary_envelope(create_envelope(body=b"abc")))
    struct.pack_into(">I", encoded, BINARY_FIXED_HEADER_BYTES - 4, 4)

    with pytest.raises(BinaryEnvelopeError, match="length does not match"):
        decode_binary_envelope(bytes(encoded))


@pytest.mark.parametrize(
    ("offset", "value", "error"),
    [
        (0, ord("X"), "magic"),
        (4, BINARY_ENVELOPE_VERSION + 1, "version"),
        (5, 99, "media type"),
    ],
)
def test_binary_envelope_rejects_unknown_header_values(
    offset: int,
    value: int,
    error: str,
) -> None:
    encoded = bytearray(encode_binary_envelope(create_envelope()))
    encoded[offset] = value

    with pytest.raises(BinaryEnvelopeError, match=error):
        decode_binary_envelope(bytes(encoded))


def test_binary_envelope_header_enforces_media_specific_body_limit() -> None:
    with pytest.raises(ValidationError, match="audio limit"):
        BinaryEnvelopeHeader(
            version=BINARY_ENVELOPE_VERSION,
            media_type=BinaryMediaType.AUDIO,
            session_id="session-1",
            input_id="input-1",
            captured_at_ms=1_234,
            format="audio/pcm;rate=16000;channels=1;format=s16le",
            body_length=MAX_AUDIO_BODY_BYTES + 1,
        )


def test_binary_envelope_rejects_non_binary_payload() -> None:
    with pytest.raises(BinaryEnvelopeError, match="must be bytes"):
        decode_binary_envelope("not-bytes")  # type: ignore[arg-type]
