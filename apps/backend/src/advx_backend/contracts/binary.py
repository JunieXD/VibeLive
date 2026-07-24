"""Versioned binary envelopes for realtime audio and image input."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from struct import Struct
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from advx_backend.application.ports.asr import AudioSource

BINARY_ENVELOPE_MAGIC: Final = b"ADVX"
BINARY_ENVELOPE_VERSION: Final = 3
MAX_SESSION_ID_BYTES: Final = 128
MAX_INPUT_ID_BYTES: Final = 128
MAX_FORMAT_BYTES: Final = 128
MAX_AUDIO_BODY_BYTES: Final = 2_097_152
MAX_IMAGE_BODY_BYTES: Final = 4_194_304
MAX_CAPTURED_AT_MS: Final = (1 << 64) - 1

_V1_FIXED_HEADER = Struct(">4sBBHHQHI")
_V2_FIXED_HEADER = Struct(">4sBBBHHQHI")
_V3_FIXED_HEADER = Struct(">4sBI")
BINARY_FIXED_HEADER_BYTES: Final = _V3_FIXED_HEADER.size
MAX_JSON_HEADER_BYTES: Final = 4_096
MAX_BINARY_ENVELOPE_BYTES: Final = (
    BINARY_FIXED_HEADER_BYTES
    + MAX_JSON_HEADER_BYTES
    + MAX_IMAGE_BODY_BYTES
)


class BinaryMediaType(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"


class BinaryEnvelopeError(ValueError):
    """Raised when a binary envelope is malformed or outside its limits."""


class BinaryPayloadTooLargeError(BinaryEnvelopeError):
    pass


class UnsupportedBinaryVersionError(BinaryEnvelopeError):
    pass


class UnsupportedBinaryMediaTypeError(BinaryEnvelopeError):
    pass


def max_body_bytes(media_type: BinaryMediaType) -> int:
    if media_type is BinaryMediaType.AUDIO:
        return MAX_AUDIO_BODY_BYTES
    if media_type is BinaryMediaType.IMAGE:
        return MAX_IMAGE_BODY_BYTES
    raise ValueError(f"unsupported media type: {media_type}")


class BinaryEnvelopeHeader(BaseModel):
    """Metadata encoded in every binary envelope; it intentionally has no body field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1, 2, 3] = BINARY_ENVELOPE_VERSION
    media_type: BinaryMediaType
    source: AudioSource | None = None
    session_id: str = Field(min_length=1, max_length=MAX_SESSION_ID_BYTES)
    input_id: str = Field(min_length=1, max_length=MAX_INPUT_ID_BYTES)
    captured_at_ms: int = Field(ge=0, le=MAX_CAPTURED_AT_MS)
    format: str = Field(min_length=1, max_length=MAX_FORMAT_BYTES)
    body_length: int = Field(ge=1)
    turn_id: str | None = Field(default=None, min_length=1, max_length=MAX_INPUT_ID_BYTES)
    system_audio_required: bool = False

    @model_validator(mode="after")
    def validate_body_length(self) -> BinaryEnvelopeHeader:
        if self.media_type is BinaryMediaType.AUDIO and self.source is None:
            object.__setattr__(self, "source", AudioSource.MICROPHONE)
        if (
            self.version == 1
            and self.media_type is BinaryMediaType.AUDIO
            and self.source is not AudioSource.MICROPHONE
        ):
            raise ValueError("binary envelope v1 only supports microphone audio")
        if self.media_type is BinaryMediaType.IMAGE and self.source is not None:
            raise ValueError("image envelopes cannot have an audio source")
        if self.version < 3 and (
            self.turn_id is not None or self.system_audio_required
        ):
            raise ValueError("coordinated audio metadata requires binary envelope v3")
        if self.media_type is BinaryMediaType.IMAGE and (
            self.turn_id is not None or self.system_audio_required
        ):
            raise ValueError("image envelopes cannot have coordinated audio metadata")
        if self.media_type is BinaryMediaType.AUDIO:
            if self.version == 3 and self.turn_id is None:
                raise ValueError("binary envelope v3 audio requires a turn_id")
            if self.source is not AudioSource.MICROPHONE and self.system_audio_required:
                raise ValueError("only microphone audio can require system audio")
            if self.system_audio_required and self.turn_id is None:
                raise ValueError("system audio requirements need a turn_id")
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
_SOURCE_TO_CODE: Final = {
    None: 0,
    AudioSource.MICROPHONE: 1,
    AudioSource.SYSTEM_AUDIO: 2,
}
_CODE_TO_SOURCE: Final = {code: source for source, code in _SOURCE_TO_CODE.items()}


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
        raise BinaryPayloadTooLargeError(
            f"binary {header.media_type.value} body exceeds the limit of {limit} bytes"
        )

    if header.version == 3:
        json_header = json.dumps(
            {
                "media_type": header.media_type.value,
                "source": None if header.source is None else header.source.value,
                "session_id": header.session_id,
                "input_id": header.input_id,
                "captured_at_ms": header.captured_at_ms,
                "format": header.format,
                "body_length": header.body_length,
                **({"turn_id": header.turn_id} if header.turn_id is not None else {}),
                **(
                    {"system_audio_required": True}
                    if header.system_audio_required
                    else {}
                ),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(json_header) > MAX_JSON_HEADER_BYTES:
            raise BinaryEnvelopeError("binary envelope JSON header is too large")
        return b"".join(
            (
                _V3_FIXED_HEADER.pack(
                    BINARY_ENVELOPE_MAGIC,
                    header.version,
                    len(json_header),
                ),
                json_header,
                envelope.body,
            )
        )

    fixed_header = (
        _V1_FIXED_HEADER.pack(
            BINARY_ENVELOPE_MAGIC,
            header.version,
            _MEDIA_TYPE_TO_CODE[header.media_type],
            len(session_id),
            len(input_id),
            header.captured_at_ms,
            len(format_value),
            header.body_length,
        )
        if header.version == 1
        else _V2_FIXED_HEADER.pack(
                BINARY_ENVELOPE_MAGIC,
                header.version,
                _MEDIA_TYPE_TO_CODE[header.media_type],
                _SOURCE_TO_CODE[header.source],
                len(session_id),
                len(input_id),
                header.captured_at_ms,
                len(format_value),
                header.body_length,
            )
    )
    return b"".join(
        (
            fixed_header,
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
    if len(payload) < _V1_FIXED_HEADER.size:
        raise BinaryEnvelopeError("binary envelope is shorter than its fixed header")
    if len(payload) > MAX_BINARY_ENVELOPE_BYTES:
        raise BinaryPayloadTooLargeError("binary envelope exceeds the maximum allowed size")

    magic = payload[:4]
    version = payload[4]
    if magic != BINARY_ENVELOPE_MAGIC:
        raise BinaryEnvelopeError("binary envelope has an invalid magic value")
    if version not in {1, 2, BINARY_ENVELOPE_VERSION}:
        raise UnsupportedBinaryVersionError("binary envelope version is not supported")
    if version == 3:
        return _decode_v3_binary_envelope(payload)
    fixed_header_bytes = (
        _V1_FIXED_HEADER.size if version == 1 else _V2_FIXED_HEADER.size
    )
    if len(payload) < fixed_header_bytes:
        raise BinaryEnvelopeError("binary envelope is shorter than its fixed header")
    if version == 1:
        (
            _,
            _,
            media_type_code,
            session_id_length,
            input_id_length,
            captured_at_ms,
            format_length,
            body_length,
        ) = _V1_FIXED_HEADER.unpack_from(payload)
        source_code = 1 if media_type_code == _MEDIA_TYPE_TO_CODE[BinaryMediaType.AUDIO] else 0
    else:
        (
            _,
            _,
            media_type_code,
            source_code,
            session_id_length,
            input_id_length,
            captured_at_ms,
            format_length,
            body_length,
        ) = _V2_FIXED_HEADER.unpack_from(payload)
    try:
        media_type = _CODE_TO_MEDIA_TYPE[media_type_code]
    except KeyError as error:
        raise UnsupportedBinaryMediaTypeError(
            "binary envelope has an unsupported media type"
        ) from error
    try:
        source = _CODE_TO_SOURCE[source_code]
    except KeyError as error:
        raise BinaryEnvelopeError("binary envelope has an unsupported audio source") from error
    if media_type is BinaryMediaType.AUDIO and source is None:
        raise BinaryEnvelopeError("binary audio envelope requires a source")
    if media_type is BinaryMediaType.IMAGE and source is not None:
        raise BinaryEnvelopeError("binary image envelope cannot have an audio source")

    _validate_wire_length(session_id_length, "session_id", MAX_SESSION_ID_BYTES)
    _validate_wire_length(input_id_length, "input_id", MAX_INPUT_ID_BYTES)
    _validate_wire_length(format_length, "format", MAX_FORMAT_BYTES)
    body_limit = max_body_bytes(media_type)
    if body_length < 1:
        raise BinaryEnvelopeError("binary envelope body length must be at least one")
    if body_length > body_limit:
        raise BinaryPayloadTooLargeError(
            f"binary {media_type.value} body exceeds the limit of {body_limit} bytes"
        )

    expected_length = (
        fixed_header_bytes
        + session_id_length
        + input_id_length
        + format_length
        + body_length
    )
    if len(payload) != expected_length:
        raise BinaryEnvelopeError("binary envelope length does not match its header")

    cursor = fixed_header_bytes
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
            source=source,
            session_id=session_id,
            input_id=input_id,
            captured_at_ms=captured_at_ms,
            format=format_value,
            body_length=body_length,
        )
    except ValidationError as error:
        raise BinaryEnvelopeError("binary envelope header is invalid") from error
    return BinaryInputEnvelope(header=header, body=body)


def _decode_v3_binary_envelope(payload: bytes) -> BinaryInputEnvelope:
    if len(payload) < _V3_FIXED_HEADER.size:
        raise BinaryEnvelopeError("binary envelope is shorter than its fixed header")
    _, _, header_length = _V3_FIXED_HEADER.unpack_from(payload)
    if header_length < 2 or header_length > MAX_JSON_HEADER_BYTES:
        raise BinaryEnvelopeError("binary envelope JSON header length is invalid")
    body_offset = _V3_FIXED_HEADER.size + header_length
    if len(payload) < body_offset:
        raise BinaryEnvelopeError("binary envelope JSON header is truncated")
    try:
        raw_header = json.loads(
            payload[_V3_FIXED_HEADER.size : body_offset].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BinaryEnvelopeError("binary envelope JSON header is invalid") from error
    if not isinstance(raw_header, dict):
        raise BinaryEnvelopeError("binary envelope JSON header must be an object")
    try:
        header = BinaryEnvelopeHeader(version=3, **raw_header)
    except (TypeError, ValidationError) as error:
        raise BinaryEnvelopeError("binary envelope header is invalid") from error
    body = payload[body_offset:]
    if len(body) != header.body_length:
        raise BinaryEnvelopeError("binary envelope length does not match its header")
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
