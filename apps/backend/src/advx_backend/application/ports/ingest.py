"""Application contracts for bounded realtime ingest.

These types deliberately keep media payloads at the ingest and frame-store
boundary. Observations contain only ``FrameRef`` values, while providers that
need pixels resolve them through ``FrameResolver``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from advx_backend.application.ports.asr import AudioSource
from advx_backend.application.visual_signature import (
    decode_visual_signature,
    validate_visual_signature,
)
from advx_backend.domain.observation import FrameRef


class IngestInputKind(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    FRAME = "frame"


class IngestReceiptStage(StrEnum):
    RECEIVED = "received"
    COMMITTED = "committed"


def _require_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must not be empty")


def _require_timestamp(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _require_media_body(value: object, field_name: str) -> None:
    if not isinstance(value, bytes) or not value:
        raise ValueError(f"{field_name} must be non-empty bytes")


@dataclass(frozen=True, slots=True)
class TextInput:
    session_id: str
    input_id: str
    created_at_ms: int
    text: str = field(repr=False)
    target_viewer_id: str | None = None
    target_persona_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.created_at_ms, "created_at_ms")
        _require_non_empty_string(self.text, "text")
        if self.target_viewer_id is not None:
            _require_non_empty_string(self.target_viewer_id, "target_viewer_id")
        if self.target_persona_id is not None:
            _require_non_empty_string(self.target_persona_id, "target_persona_id")
        if self.target_viewer_id is not None and self.target_persona_id is not None:
            raise ValueError("text input can target either a Viewer or a Persona")


@dataclass(frozen=True, slots=True)
class AudioInput:
    session_id: str
    input_id: str
    captured_at_ms: int
    format: str
    body: bytes = field(repr=False)
    source: AudioSource = AudioSource.MICROPHONE
    turn_id: str | None = None
    system_audio_required: bool = False
    connection_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.format, "format")
        _require_media_body(self.body, "body")
        object.__setattr__(self, "source", AudioSource(self.source))
        if self.turn_id is not None:
            _require_non_empty_string(self.turn_id, "turn_id")
        if not isinstance(self.system_audio_required, bool):
            raise ValueError("system_audio_required must be a boolean")
        if self.source is not AudioSource.MICROPHONE and self.system_audio_required:
            raise ValueError("only microphone audio can require system audio")
        if self.system_audio_required and self.turn_id is None:
            raise ValueError("system audio requirements need a turn_id")
        if self.connection_id is not None:
            _require_non_empty_string(self.connection_id, "connection_id")


@dataclass(frozen=True, slots=True)
class AudioCommit:
    session_id: str
    input_id: str
    committed_at_ms: int
    source: AudioSource = AudioSource.MICROPHONE
    turn_id: str | None = None
    system_audio_required: bool = False
    connection_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.committed_at_ms, "committed_at_ms")
        object.__setattr__(self, "source", AudioSource(self.source))
        if self.turn_id is not None:
            _require_non_empty_string(self.turn_id, "turn_id")
        if not isinstance(self.system_audio_required, bool):
            raise ValueError("system_audio_required must be a boolean")
        if self.source is not AudioSource.MICROPHONE and self.system_audio_required:
            raise ValueError("only microphone audio can require system audio")
        if self.system_audio_required and self.turn_id is None:
            raise ValueError("system audio requirements need a turn_id")
        if self.connection_id is not None:
            _require_non_empty_string(self.connection_id, "connection_id")


@dataclass(frozen=True, slots=True)
class FrameInput:
    session_id: str
    input_id: str
    captured_at_ms: int
    mime_type: str
    body: bytes = field(repr=False)
    change_score: float | None = None
    visual_signature: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.mime_type, "mime_type")
        _require_media_body(self.body, "body")
        mime_type, transported_score, transported_signature = _parse_frame_mime_type(
            self.mime_type
        )
        if transported_score is not None and self.change_score is not None:
            raise ValueError("change_score must use either metadata or the explicit field")
        if transported_signature is not None and self.visual_signature is not None:
            raise ValueError("visual_signature must use either metadata or the explicit field")
        change_score = (
            transported_score if transported_score is not None else self.change_score
        )
        visual_signature = (
            transported_signature
            if transported_signature is not None
            else self.visual_signature
        )
        if change_score is not None and (
            isinstance(change_score, bool)
            or not isinstance(change_score, (int, float))
            or not math.isfinite(change_score)
            or not 0 <= change_score <= 1
        ):
            raise ValueError("change_score must be a finite number between zero and one")
        if visual_signature is not None:
            validate_visual_signature(visual_signature)
        object.__setattr__(self, "mime_type", mime_type)
        object.__setattr__(
            self,
            "change_score",
            None if change_score is None else float(change_score),
        )
        object.__setattr__(self, "visual_signature", visual_signature)


def _parse_frame_mime_type(value: str) -> tuple[str, float | None, bytes | None]:
    mime_type, *parameters = value.split(";")
    normalized = mime_type.strip().casefold()
    if not normalized:
        raise ValueError("frame MIME type is invalid")
    change_score: float | None = None
    visual_signature: bytes | None = None
    for parameter in parameters:
        if parameter.strip() != parameter:
            raise ValueError("frame MIME metadata is invalid")
        key, separator, raw_value = parameter.partition("=")
        if not separator or not key or not raw_value or raw_value.strip() != raw_value:
            raise ValueError("frame MIME metadata is invalid")
        if key == "advx-change-score":
            if change_score is not None:
                raise ValueError("frame change score metadata is duplicated")
            try:
                change_score = float(raw_value)
            except ValueError as error:
                raise ValueError("frame change score metadata is invalid") from error
        elif key == "advx-visual-signature":
            if visual_signature is not None:
                raise ValueError("frame visual signature metadata is duplicated")
            try:
                visual_signature = decode_visual_signature(raw_value)
            except ValueError as error:
                raise ValueError("frame visual signature metadata is invalid") from error
        else:
            raise ValueError("frame MIME metadata is invalid")
    return normalized, change_score, visual_signature


@dataclass(frozen=True, slots=True)
class IngestReceipt:
    session_id: str
    input_id: str
    input_kind: IngestInputKind
    stage: IngestReceiptStage
    accepted_at_ms: int

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.accepted_at_ms, "accepted_at_ms")
        object.__setattr__(self, "input_kind", IngestInputKind(self.input_kind))
        object.__setattr__(self, "stage", IngestReceiptStage(self.stage))


@dataclass(frozen=True, slots=True)
class FrameStoreLimits:
    """Limits that make a frame store bounded by count and bytes."""

    max_frames: int
    max_frame_bytes: int
    max_total_bytes: int

    def __post_init__(self) -> None:
        if self.max_frames < 1:
            raise ValueError("max_frames must be at least one")
        if self.max_frame_bytes < 1:
            raise ValueError("max_frame_bytes must be at least one")
        if self.max_total_bytes < 1:
            raise ValueError("max_total_bytes must be at least one")


@dataclass(frozen=True, slots=True)
class ResolvedFrame:
    """Frame bytes returned only to the adapter that explicitly resolves a ref."""

    session_id: str
    frame_id: str
    input_id: str
    captured_at_ms: int
    mime_type: str
    body: bytes = field(repr=False)
    change_score: float = 0.0
    visual_signature: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.frame_id, "frame_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.mime_type, "mime_type")
        _require_media_body(self.body, "body")
        if not 0 <= self.change_score <= 1:
            raise ValueError("change_score must be between zero and one")
        if self.visual_signature is not None:
            validate_visual_signature(self.visual_signature)


class IngestPort(Protocol):
    """Application entry point implemented later by ``IngestService``."""

    async def submit_text(self, input: TextInput) -> IngestReceipt: ...

    async def submit_audio(self, input: AudioInput) -> IngestReceipt: ...

    async def submit_audio_and_commit(self, input: AudioInput) -> IngestReceipt: ...

    async def commit_audio(self, commit: AudioCommit) -> IngestReceipt: ...

    async def clear_connection(self, connection_id: str) -> None: ...

    async def notify_voice_activity(
        self,
        session_id: str,
        occurred_at_ms: int,
        source: AudioSource = AudioSource.MICROPHONE,
    ) -> None: ...

    async def submit_frame(self, input: FrameInput) -> IngestReceipt: ...


class FrameStore(Protocol):
    """Stores frame bodies in bounded ephemeral memory and returns opaque refs."""

    @property
    def limits(self) -> FrameStoreLimits: ...

    async def start_session(self, session_id: str) -> None: ...

    async def stop_session(self, session_id: str) -> None: ...

    async def store(self, frame: FrameInput) -> FrameRef: ...

    async def clear_session(self, session_id: str) -> None: ...


class FrameResolver(Protocol):
    """Resolves an opaque observation reference without exposing bodies in it."""

    async def resolve(
        self,
        *,
        session_id: str,
        frame: FrameRef,
    ) -> ResolvedFrame | None: ...
