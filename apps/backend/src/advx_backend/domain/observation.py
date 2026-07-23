from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from advx_backend.domain.room import RoomEvent


@dataclass(frozen=True, slots=True)
class FrameRef:
    frame_id: str
    created_at_ms: int
    mime_type: str
    data_ref: str

    def __post_init__(self) -> None:
        if not self.frame_id:
            raise ValueError("frame_id must not be empty")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must not be negative")
        if not self.mime_type:
            raise ValueError("mime_type must not be empty")
        if not self.data_ref:
            raise ValueError("data_ref must not be empty")


@dataclass(frozen=True, slots=True)
class Observation:
    session_id: str
    observation_id: str
    created_at_ms: int
    frames: tuple[FrameRef, ...] = ()
    room_events: tuple[RoomEvent, ...] = ()
    user_context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if not self.observation_id:
            raise ValueError("observation_id must not be empty")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must not be negative")

        frames = tuple(self.frames)
        room_events = tuple(self.room_events)
        for event in room_events:
            if event.session_id != self.session_id:
                raise ValueError("room events must belong to the observation session")

        user_context = dict(self.user_context)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in user_context.items()
        ):
            raise TypeError("user_context keys and values must be strings")

        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "room_events", room_events)
        object.__setattr__(self, "user_context", MappingProxyType(user_context))
