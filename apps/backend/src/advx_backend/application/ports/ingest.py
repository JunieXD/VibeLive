"""Application contracts for bounded realtime ingest.

These types deliberately keep media payloads at the ingest and frame-store
boundary. Observations contain only ``FrameRef`` values, while providers that
need pixels resolve them through ``FrameResolver``.
"""

from __future__ import annotations

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

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.created_at_ms, "created_at_ms")
        _require_non_empty_string(self.text, "text")


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

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.mime_type, "mime_type")
        _require_media_body(self.body, "body")


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

    def __post_init__(self) -> None:
        _require_non_empty_string(self.session_id, "session_id")
        _require_non_empty_string(self.frame_id, "frame_id")
        _require_non_empty_string(self.input_id, "input_id")
        _require_timestamp(self.captured_at_ms, "captured_at_ms")
        _require_non_empty_string(self.mime_type, "mime_type")
        _require_media_body(self.body, "body")


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
