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
    trigger_event_ids: tuple[str, ...] = ()
    trigger_frame_ids: tuple[str, ...] = ()
    user_context: Mapping[str, str] = field(default_factory=dict)
    target_viewer_id: str | None = None
    target_persona_id: str | None = None
    target_ambiguous: bool = False

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if not self.observation_id:
            raise ValueError("observation_id must not be empty")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must not be negative")
        if self.target_viewer_id == "":
            raise ValueError("target_viewer_id must not be empty")
        if self.target_persona_id == "":
            raise ValueError("target_persona_id must not be empty")
        if self.target_viewer_id is not None and self.target_persona_id is not None:
            raise ValueError("an observation can target either a Viewer or a Persona")
        if self.target_ambiguous and (
            self.target_viewer_id is not None or self.target_persona_id is not None
        ):
            raise ValueError("an ambiguous target must use ordinary broadcast")

        frames = tuple(self.frames)
        room_events = tuple(self.room_events)
        trigger_event_ids = tuple(self.trigger_event_ids)
        trigger_frame_ids = tuple(self.trigger_frame_ids)
        for event in room_events:
            if event.session_id != self.session_id:
                raise ValueError("room events must belong to the observation session")
        if not set(trigger_event_ids).issubset(
            event.event_id for event in room_events
        ):
            raise ValueError("trigger_event_ids must reference observation room events")
        if not set(trigger_frame_ids).issubset(frame.frame_id for frame in frames):
            raise ValueError("trigger_frame_ids must reference observation frames")
        if len(set(trigger_event_ids)) != len(trigger_event_ids):
            raise ValueError("trigger_event_ids must be unique")
        if len(set(trigger_frame_ids)) != len(trigger_frame_ids):
            raise ValueError("trigger_frame_ids must be unique")

        user_context = dict(self.user_context)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in user_context.items()
        ):
            raise TypeError("user_context keys and values must be strings")

        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "room_events", room_events)
        object.__setattr__(self, "trigger_event_ids", trigger_event_ids)
        object.__setattr__(self, "trigger_frame_ids", trigger_frame_ids)
        object.__setattr__(self, "user_context", MappingProxyType(user_context))
