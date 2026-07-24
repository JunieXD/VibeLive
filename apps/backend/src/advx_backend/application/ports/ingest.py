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

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.format, "format")
        _require_media_body(self.body, "body")


@dataclass(frozen=True, slots=True)
class AudioCommit:
    session_id: str
    input_id: str
    committed_at_ms: int

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.committed_at_ms, "committed_at_ms")


@dataclass(frozen=True, slots=True)
class FrameInput:
    session_id: str
    input_id: str
    captured_at_ms: int
    mime_type: str
    body: bytes = field(repr=False)
    change_score: float | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.mime_type, "mime_type")
        _require_media_body(self.body, "body")
        mime_type, transported_score = _parse_frame_mime_type(self.mime_type)
        if transported_score is not None and self.change_score is not None:
            raise ValueError("change_score must use either metadata or the explicit field")
        change_score = (
            transported_score if transported_score is not None else self.change_score
        )
        if change_score is not None and (
            isinstance(change_score, bool)
            or not isinstance(change_score, (int, float))
            or not math.isfinite(change_score)
            or not 0 <= change_score <= 1
        ):
            raise ValueError("change_score must be a finite number between zero and one")
        object.__setattr__(self, "mime_type", mime_type)
        object.__setattr__(
            self,
            "change_score",
            None if change_score is None else float(change_score),
        )


def _parse_frame_mime_type(value: str) -> tuple[str, float | None]:
    mime_type, separator, metadata = value.partition(";")
    normalized = mime_type.strip().casefold()
    if not separator:
        return normalized, None
    if not metadata.startswith("advx-change-score=") or ";" in metadata:
        raise ValueError("frame MIME metadata is invalid")
    raw_score = metadata.removeprefix("advx-change-score=")
    if not raw_score or raw_score.strip() != raw_score:
        raise ValueError("frame change score metadata is invalid")
    try:
        score = float(raw_score)
    except ValueError as error:
        raise ValueError("frame change score metadata is invalid") from error
    return normalized, score


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

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.frame_id, "frame_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.mime_type, "mime_type")
        _require_media_body(self.body, "body")
        if not 0 <= self.change_score <= 1:
            raise ValueError("change_score must be between zero and one")


class IngestPort(Protocol):
    """Application entry point implemented later by ``IngestService``."""

    async def submit_text(self, input: TextInput) -> IngestReceipt: ...

    async def submit_audio(self, input: AudioInput) -> IngestReceipt: ...

    async def commit_audio(self, commit: AudioCommit) -> IngestReceipt: ...

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
