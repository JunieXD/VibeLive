"""Versioned binary envelopes for realtime audio and image input."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from struct import Struct
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

BINARY_ENVELOPE_MAGIC: Final = b"ADVX"
BINARY_ENVELOPE_VERSION: Final = 1
MAX_SESSION_ID_BYTES: Final = 128
MAX_INPUT_ID_BYTES: Final = 128
MAX_FORMAT_BYTES: Final = 128
MAX_AUDIO_BODY_BYTES: Final = 1_048_576
MAX_IMAGE_BODY_BYTES: Final = 4_194_304
MAX_CAPTURED_AT_MS: Final = (1 << 64) - 1

_FIXED_HEADER = Struct(">4sBBHHQHI")
BINARY_FIXED_HEADER_BYTES: Final = _FIXED_HEADER.size
MAX_BINARY_ENVELOPE_BYTES: Final = (
    BINARY_FIXED_HEADER_BYTES
    + MAX_SESSION_ID_BYTES
    + MAX_INPUT_ID_BYTES
    + MAX_FORMAT_BYTES
    + MAX_IMAGE_BODY_BYTES
)


class BinaryMediaType(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"


class BinaryEnvelopeError(ValueError):
    """Raised when a binary envelope is malformed or outside its limits."""


def max_body_bytes(media_type: BinaryMediaType) -> int:
    if media_type is BinaryMediaType.AUDIO:
        return MAX_AUDIO_BODY_BYTES
    if media_type is BinaryMediaType.IMAGE:
        return MAX_IMAGE_BODY_BYTES
    raise ValueError(f"unsupported media type: {media_type}")


class BinaryEnvelopeHeader(BaseModel):
    """Metadata encoded in every binary envelope; it intentionally has no body field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = BINARY_ENVELOPE_VERSION
    media_type: BinaryMediaType
    session_id: str = Field(min_length=1, max_length=MAX_SESSION_ID_BYTES)
    input_id: str = Field(min_length=1, max_length=MAX_INPUT_ID_BYTES)
    captured_at_ms: int = Field(ge=0, le=MAX_CAPTURED_AT_MS)
    format: str = Field(min_length=1, max_length=MAX_FORMAT_BYTES)
    body_length: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_body_length(self) -> BinaryEnvelopeHeader:
        limit = max_body_bytes(self.media_type)
        if self.body_length > limit:
            raise ValueError(
                f"body_length exceeds the {self.media_type.value} limit of {limit} bytes"
            )
        return self


@dataclass(frozen=True, slots=True)
class BinaryInputEnvelope:
    """Decoded envelope whose body stays out of repr and structured metadata."""

    header: BinaryEnvelopeHeader
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes):
            raise BinaryEnvelopeError("binary envelope body must be bytes")
        if len(self.body) != self.header.body_length:
            raise BinaryEnvelopeError("binary envelope body length does not match its header")


_MEDIA_TYPE_TO_CODE: Final = {
    BinaryMediaType.AUDIO: 1,
    BinaryMediaType.IMAGE: 2,
}
_CODE_TO_MEDIA_TYPE: Final = {code: media_type for media_type, code in _MEDIA_TYPE_TO_CODE.items()}


def encode_binary_envelope(envelope: BinaryInputEnvelope) -> bytes:
    """Encode one complete WebSocket binary message without logging its body."""

    header = envelope.header
    session_id = _encode_wire_text(
        header.session_id,
        field_name="session_id",
        limit=MAX_SESSION_ID_BYTES,
    )
    input_id = _encode_wire_text(
        header.input_id,
        field_name="input_id",
        limit=MAX_INPUT_ID_BYTES,
    )
    format_value = _encode_wire_text(
        header.format,
        field_name="format",
        limit=MAX_FORMAT_BYTES,
    )
    limit = max_body_bytes(header.media_type)
    if len(envelope.body) > limit:
        raise BinaryEnvelopeError(
            f"binary {header.media_type.value} body exceeds the limit of {limit} bytes"
        )

    return b"".join(
        (
            _FIXED_HEADER.pack(
                BINARY_ENVELOPE_MAGIC,
                header.version,
                _MEDIA_TYPE_TO_CODE[header.media_type],
                len(session_id),
                len(input_id),
                header.captured_at_ms,
                len(format_value),
                header.body_length,
            ),
            session_id,
            input_id,
            format_value,
            envelope.body,
        )
    )


def decode_binary_envelope(payload: bytes) -> BinaryInputEnvelope:
    """Decode and validate one complete WebSocket binary message."""

    if not isinstance(payload, bytes):
        raise BinaryEnvelopeError("binary envelope payload must be bytes")
    if len(payload) < BINARY_FIXED_HEADER_BYTES:
        raise BinaryEnvelopeError("binary envelope is shorter than its fixed header")
    if len(payload) > MAX_BINARY_ENVELOPE_BYTES:
        raise BinaryEnvelopeError("binary envelope exceeds the maximum allowed size")

    (
        magic,
        version,
        media_type_code,
        session_id_length,
        input_id_length,
        captured_at_ms,
        format_length,
        body_length,
    ) = _FIXED_HEADER.unpack_from(payload)
    if magic != BINARY_ENVELOPE_MAGIC:
        raise BinaryEnvelopeError("binary envelope has an invalid magic value")
    if version != BINARY_ENVELOPE_VERSION:
        raise BinaryEnvelopeError("binary envelope version is not supported")
    try:
        media_type = _CODE_TO_MEDIA_TYPE[media_type_code]
    except KeyError as error:
        raise BinaryEnvelopeError("binary envelope has an unsupported media type") from error

    _validate_wire_length(session_id_length, "session_id", MAX_SESSION_ID_BYTES)
    _validate_wire_length(input_id_length, "input_id", MAX_INPUT_ID_BYTES)
    _validate_wire_length(format_length, "format", MAX_FORMAT_BYTES)
    body_limit = max_body_bytes(media_type)
    if body_length < 1:
        raise BinaryEnvelopeError("binary envelope body length must be at least one")
    if body_length > body_limit:
        raise BinaryEnvelopeError(
            f"binary {media_type.value} body exceeds the limit of {body_limit} bytes"
        )

    expected_length = (
        BINARY_FIXED_HEADER_BYTES
        + session_id_length
        + input_id_length
        + format_length
        + body_length
    )
    if len(payload) != expected_length:
        raise BinaryEnvelopeError("binary envelope length does not match its header")

    cursor = BINARY_FIXED_HEADER_BYTES
    session_id = _decode_wire_text(payload[cursor : cursor + session_id_length], "session_id")
    cursor += session_id_length
    input_id = _decode_wire_text(payload[cursor : cursor + input_id_length], "input_id")
    cursor += input_id_length
    format_value = _decode_wire_text(payload[cursor : cursor + format_length], "format")
    cursor += format_length
    body = payload[cursor:]

    try:
        header = BinaryEnvelopeHeader(
            version=version,
            media_type=media_type,
            session_id=session_id,
            input_id=input_id,
            captured_at_ms=captured_at_ms,
            format=format_value,
            body_length=body_length,
        )
    except ValidationError as error:
        raise BinaryEnvelopeError("binary envelope header is invalid") from error
    return BinaryInputEnvelope(header=header, body=body)


def _encode_wire_text(value: str, *, field_name: str, limit: int) -> bytes:
    if not value or "\x00" in value:
        raise BinaryEnvelopeError(f"binary envelope {field_name} must be non-empty text")
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        raise BinaryEnvelopeError(
            f"binary envelope {field_name} exceeds the limit of {limit} bytes"
        )
    return encoded


def _decode_wire_text(value: bytes, field_name: str) -> str:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BinaryEnvelopeError(f"binary envelope {field_name} is not valid UTF-8") from error
    if not decoded or "\x00" in decoded:
        raise BinaryEnvelopeError(f"binary envelope {field_name} must be non-empty text")
    return decoded


def _validate_wire_length(value: int, field_name: str, limit: int) -> None:
    if value < 1 or value > limit:
        raise BinaryEnvelopeError(
            f"binary envelope {field_name} length must be between 1 and {limit} bytes"
        )
