from dataclasses import dataclass
from enum import StrEnum


class BarrageRejectionReason(StrEnum):
    AUDIENCE_NOT_IN_REQUEST = "audience_not_in_request"
    SESSION_MISMATCH = "session_mismatch"
    OBSERVATION_MISMATCH = "observation_mismatch"
    REQUEST_MISMATCH = "request_mismatch"
    EXPIRED = "expired"
    OBSERVATION_IN_FUTURE = "observation_in_future"
    INVALID_TEXT = "invalid_text"
    EMPTY_TEXT = "empty_text"
    TEXT_TOO_LONG = "text_too_long"
    BLOCKED_WORD = "blocked_word"
    DUPLICATE = "duplicate"
    DENSITY_LIMIT_EXCEEDED = "density_limit_exceeded"


@dataclass(frozen=True, slots=True, kw_only=True)
class BarragePolicy:
    max_text_length: int
    ttl_ms: int
    blocked_words: frozenset[str]
    duplicate_window_ms: int
    max_duplicate_entries_per_session: int
    density_window_ms: int
    max_outputs_per_density_window: int
    max_tracked_sessions: int

    def __post_init__(self) -> None:
        if self.max_text_length < 1:
            raise ValueError("max_text_length must be at least one")
        if self.ttl_ms < 1:
            raise ValueError("ttl_ms must be at least one")
        if self.duplicate_window_ms < 0:
            raise ValueError("duplicate_window_ms cannot be negative")
        if self.max_duplicate_entries_per_session < 1:
            raise ValueError("max_duplicate_entries_per_session must be at least one")
        if self.density_window_ms < 1:
            raise ValueError("density_window_ms must be at least one")
        if self.max_outputs_per_density_window < 0:
            raise ValueError("max_outputs_per_density_window cannot be negative")
        if self.max_tracked_sessions < 1:
            raise ValueError("max_tracked_sessions must be at least one")

        normalized_words: set[str] = set()
        for word in self.blocked_words:
            if not isinstance(word, str) or not word.strip():
                raise ValueError("blocked_words cannot contain blank or non-string values")
            normalized_words.add(word.strip().casefold())
        object.__setattr__(self, "blocked_words", frozenset(normalized_words))


@dataclass(frozen=True, slots=True, kw_only=True)
class BarrageValidationScope:
    session_id: str
    observation_id: str
    request_id: str

    def __post_init__(self) -> None:
        for field_name in ("session_id", "observation_id", "request_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")


class BarrageEvidenceSource(StrEnum):
    EVENT = "event"
    FRAME = "frame"


@dataclass(frozen=True, slots=True, kw_only=True)
class BarrageEvidenceRef:
    source: BarrageEvidenceSource
    event_id: str | None = None
    frame_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", BarrageEvidenceSource(self.source))
        if self.source is BarrageEvidenceSource.EVENT:
            if not self.event_id or self.frame_index is not None:
                raise ValueError("event evidence requires only event_id")
        elif self.frame_index is None or self.frame_index < 0 or self.event_id is not None:
            raise ValueError("frame evidence requires only a non-negative frame_index")


@dataclass(frozen=True, slots=True, kw_only=True)
class BarrageEvent:
    barrage_id: str
    room_id: str
    session_id: str
    audience_epoch: int
    observation_id: str
    generation_request_id: str
    viewer_instance_id: str
    persona_id: str
    display_name: str
    viewer_sequence: int
    reaction_type: str
    evidence_refs: tuple[BarrageEvidenceRef, ...]
    text: str
    created_at_ms: int
    expires_at_ms: int
    intent: str = "react_to_scene"
    target_kind: str | None = None
    target_viewer_instance_id: str | None = None
    target_event_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "barrage_id",
            "room_id",
            "session_id",
            "observation_id",
            "generation_request_id",
            "viewer_instance_id",
            "persona_id",
            "display_name",
            "reaction_type",
            "text",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.audience_epoch < 1:
            raise ValueError("audience_epoch must be at least one")
        if self.viewer_sequence < 1:
            raise ValueError("viewer_sequence must be at least one")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must not be negative")
        if self.expires_at_ms <= self.created_at_ms:
            raise ValueError("expires_at_ms must be later than created_at_ms")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))

    @property
    def request_id(self) -> str:
        return self.generation_request_id

    @property
    def audience_id(self) -> str:
        return self.viewer_instance_id


@dataclass(frozen=True, slots=True, kw_only=True)
class BarrageRejection:
    reason: BarrageRejectionReason
    candidate_index: int
    audience_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BarrageValidationResult:
    events: tuple[BarrageEvent, ...]
    rejections: tuple[BarrageRejection, ...]
    batch_rejection_reason: BarrageRejectionReason | None = None
