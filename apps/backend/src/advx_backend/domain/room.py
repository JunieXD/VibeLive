from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias


class RoomEventSource(StrEnum):
    USER_TEXT = "user_text"
    USER_VOICE = "user_voice"
    AUDIENCE_BARRAGE = "audience_barrage"
    SCREEN_OBSERVATION = "screen_observation"
    SYSTEM_EVENT = "system_event"


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def _freeze_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("payload keys must be strings")
            frozen[key] = _freeze_json_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json_value(item) for item in value)
    raise TypeError("payload values must be JSON-compatible")


def freeze_payload(payload: Mapping[str, object]) -> Mapping[str, JsonValue]:
    frozen = _freeze_json_value(payload)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded by the argument type
        raise TypeError("payload must be a mapping")
    return frozen


@dataclass(frozen=True, slots=True)
class RoomEvent:
    event_id: str
    session_id: str
    sequence: int
    source_type: RoomEventSource
    created_at_ms: int
    source_id: str | None = None
    text: str | None = None
    payload: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must not be empty")
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if self.sequence < 1:
            raise ValueError("sequence must be at least one")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must not be negative")
        if self.source_id == "":
            raise ValueError("source_id must not be empty")

        object.__setattr__(self, "source_type", RoomEventSource(self.source_type))
        object.__setattr__(self, "payload", freeze_payload(self.payload))
