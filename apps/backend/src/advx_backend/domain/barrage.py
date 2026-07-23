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


@dataclass(frozen=True, slots=True, kw_only=True)
class BarrageEvent:
    barrage_id: str
    session_id: str
    observation_id: str
    request_id: str
    audience_id: str
    text: str
    created_at_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        for field_name in (
            "barrage_id",
            "session_id",
            "observation_id",
            "request_id",
            "audience_id",
            "text",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must not be negative")
        if self.expires_at_ms <= self.created_at_ms:
            raise ValueError("expires_at_ms must be later than created_at_ms")


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
