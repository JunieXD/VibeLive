import hashlib
import json
from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from advx_backend.application.ports.asr import AudioSource
from advx_backend.application.runtime_state import RuntimeStateStore
from advx_backend.domain.room import JsonValue, RoomEvent, RoomEventSource
from advx_backend.infrastructure.persistence.sqlite.runtime_repositories import (
    PersistedRoomEvent,
    SQLiteRoomEventRepository,
    canonical_json,
)


class RoomEventRecoveryReader(Protocol):
    async def load_for_recovery(
        self,
        *,
        room_id: str,
        session_id: str,
        maximum_audience_epoch: int,
    ) -> tuple[RoomEvent, ...]: ...


class RoomEventPersistenceUnavailableError(RuntimeError):
    code = "runtime_persistence_unavailable"


class _PayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _UserTextPayload(_PayloadModel):
    input_id: str | None = None
    target_viewer_id: str | None = None
    target_persona_id: str | None = None


class _UserVoicePayload(_PayloadModel):
    audio_source: Literal[
        AudioSource.MICROPHONE,
        AudioSource.SYSTEM_AUDIO,
    ] | None = None
    final: bool | None = None
    started_at_ms: int | None = Field(default=None, ge=0)
    ended_at_ms: int | None = Field(default=None, ge=0)
    utterance_id: str | None = None
    revision: int | None = Field(default=None, ge=0)
    target_resolver_id: str | None = None
    target_ambiguous: bool | None = None
    target_viewer_id: str | None = None
    target_persona_id: str | None = None


class _EvidenceRefPayload(_PayloadModel):
    source: Literal["event", "frame"]
    event_id: str | None = None
    frame_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scope(self) -> "_EvidenceRefPayload":
        if self.source == "event":
            if self.event_id is None or self.frame_index is not None:
                raise ValueError("event evidence requires only event_id")
        elif self.frame_index is None or self.event_id is not None:
            raise ValueError("frame evidence requires only frame_index")
        return self


class _ViewerReactionTargetPayload(_PayloadModel):
    kind: Literal["host", "scene", "room", "viewer", "event"]
    viewer_instance_id: str | None = None
    event_id: str | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "_ViewerReactionTargetPayload":
        if self.kind == "viewer" and self.viewer_instance_id is None:
            raise ValueError("viewer target requires viewer_instance_id")
        if self.kind == "event" and self.event_id is None:
            raise ValueError("event target requires event_id")
        if self.kind != "viewer" and self.viewer_instance_id is not None:
            raise ValueError("viewer_instance_id requires viewer target")
        if self.kind != "event" and self.event_id is not None:
            raise ValueError("event_id requires event target")
        return self


class _AudienceBarragePayload(_PayloadModel):
    barrage_id: str | None = None
    audience_epoch: int | None = Field(default=None, ge=1)
    observation_id: str | None = None
    request_id: str | None = None
    generation_request_id: str | None = None
    viewer_instance_id: str | None = None
    persona_id: str | None = None
    display_name: str | None = None
    viewer_sequence: int | None = Field(default=None, ge=1)
    reaction_type: str | None = None
    intent: Literal[
        "react_to_host",
        "react_to_scene",
        "reply_to_viewer",
        "ask_question",
        "agree",
        "disagree",
        "encourage",
        "joke",
        "continue_thread",
        "room_meta",
        "silence",
    ] | None = None
    target: _ViewerReactionTargetPayload | None = None
    evidence_refs: list[_EvidenceRefPayload] | None = None
    expires_at_ms: int | None = Field(default=None, ge=0)


class _ScreenObservationPayload(_PayloadModel):
    frame_id: str | None = None
    frame_hash: str | None = None
    captured_at_ms: int | None = Field(default=None, ge=0)
    summary: str | None = None
    labels: list[str] | None = None


class _SystemEventPayload(_PayloadModel):
    event: str | None = None
    reason: str | None = None
    revision: int | None = Field(default=None, ge=0)
    state: str | None = None
    mode_id: str | None = None
    round: int | None = Field(default=None, ge=0)
    tags: list[str] | None = None


class PersistentRuntimeRoomEventStore:
    """Persist and validate the bounded public Room event chain."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        runtime_state: RuntimeStateStore,
        max_events: int | None,
        event_ttl_ms: int | None,
        max_text_chars: int = 4_000,
        max_content_bytes: int = 32_768,
    ) -> None:
        if max_events is not None and max_events < 1:
            raise ValueError("max_events must be at least one")
        if event_ttl_ms is not None and event_ttl_ms < 1:
            raise ValueError("event_ttl_ms must be at least one")
        if max_text_chars < 1 or max_content_bytes < 1:
            raise ValueError("room event size limits must be positive")
        self._session_factory = session_factory
        self._runtime_state = runtime_state
        self._max_events = max_events
        self._event_ttl_ms = event_ttl_ms
        self._max_text_chars = max_text_chars
        self._max_content_bytes = max_content_bytes

    async def persist(self, event: RoomEvent) -> None:
        async with self._session_factory() as session:
            await self.persist_in_session(event, session)
            await session.commit()

    async def persist_in_session(
        self,
        event: RoomEvent,
        session: AsyncSession,
    ) -> None:
        try:
            runtime = await self._runtime_state.snapshot(event.session_id)
        except KeyError as error:
            raise RoomEventPersistenceUnavailableError(
                "a committed runtime snapshot is required before persisting room events"
            ) from error
        if not runtime.accepting_results:
            raise RuntimeError("room event does not belong to an accepting runtime")
        persisted = persisted_room_event(
            event,
            room_id=runtime.spec.room.room_id,
            audience_epoch=runtime.audience_epoch,
            max_text_chars=self._max_text_chars,
            max_content_bytes=self._max_content_bytes,
        )
        repository = SQLiteRoomEventRepository(session)
        await repository.append(persisted)
        if self._event_ttl_ms is not None and self._max_events is not None:
            await repository.prune(
                persisted.room_id,
                keep_after_ms=event.created_at_ms - self._event_ttl_ms,
                max_events=self._max_events,
            )

    async def load_for_recovery(
        self,
        *,
        room_id: str,
        session_id: str,
        maximum_audience_epoch: int,
    ) -> tuple[RoomEvent, ...]:
        if maximum_audience_epoch < 1:
            raise ValueError("maximum_audience_epoch must be positive")
        async with self._session_factory() as session:
            rows = await SQLiteRoomEventRepository(session).list_for_recovery(
                room_id,
                session_id,
                limit=self._max_events,
            )
        events: list[RoomEvent] = []
        previous_sequence = 0
        for row in rows:
            event = restore_room_event(
                row,
                expected_room_id=room_id,
                expected_session_id=session_id,
                maximum_audience_epoch=maximum_audience_epoch,
            )
            if event.sequence <= previous_sequence:
                raise ValueError("persisted room event sequence is not strictly increasing")
            previous_sequence = event.sequence
            events.append(event)
        return tuple(events)


def persisted_room_event(
    event: RoomEvent,
    *,
    room_id: str,
    audience_epoch: int,
    max_text_chars: int = 4_000,
    max_content_bytes: int = 32_768,
) -> PersistedRoomEvent:
    if not room_id:
        raise ValueError("room_id must not be empty")
    if audience_epoch < 1:
        raise ValueError("audience_epoch must be positive")
    if event.text is not None and len(event.text) > max_text_chars:
        raise ValueError("room event text exceeds the persistence limit")
    _validate_persisted_payload(event.source_type, event.payload)
    content = {
        "schema_version": 1,
        "event_id": event.event_id,
        "room_id": room_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "source_type": event.source_type.value,
        "source_id": event.source_id,
        "audience_epoch": audience_epoch,
        "text": event.text,
        "payload": _thaw(event.payload),
        "occurred_at_ms": event.created_at_ms,
    }
    content_json = canonical_json(content)
    if len(content_json.encode("utf-8")) > max_content_bytes:
        raise ValueError("room event content exceeds the persistence limit")
    return PersistedRoomEvent(
        event_id=event.event_id,
        room_id=room_id,
        session_id=event.session_id,
        sequence=event.sequence,
        source_type=event.source_type.value,
        source_id=event.source_id or "",
        audience_epoch=audience_epoch,
        content_json=content_json,
        content_hash=hashlib.sha256(content_json.encode("utf-8")).hexdigest(),
        occurred_at_ms=event.created_at_ms,
    )


def restore_room_event(
    row: PersistedRoomEvent,
    *,
    expected_room_id: str,
    expected_session_id: str,
    maximum_audience_epoch: int,
) -> RoomEvent:
    expected_hash = hashlib.sha256(row.content_json.encode("utf-8")).hexdigest()
    if expected_hash != row.content_hash:
        raise ValueError("persisted room event content hash does not match")
    try:
        content = json.loads(row.content_json)
    except json.JSONDecodeError:
        raise ValueError("persisted room event content is not valid JSON") from None
    if not isinstance(content, dict) or content.get("schema_version") != 1:
        raise ValueError("persisted room event schema is unsupported")
    expected = {
        "event_id": row.event_id,
        "room_id": row.room_id,
        "session_id": row.session_id,
        "sequence": row.sequence,
        "source_type": row.source_type,
        "source_id": row.source_id or None,
        "audience_epoch": row.audience_epoch,
        "occurred_at_ms": row.occurred_at_ms,
    }
    if any(content.get(key) != value for key, value in expected.items()):
        raise ValueError("persisted room event envelope does not match its row")
    if (
        row.room_id != expected_room_id
        or row.session_id != expected_session_id
        or row.audience_epoch < 1
        or row.audience_epoch > maximum_audience_epoch
    ):
        raise ValueError("persisted room event scope is invalid")
    payload = content.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("persisted room event payload is invalid")
    text = content.get("text")
    if text is not None and not isinstance(text, str):
        raise ValueError("persisted room event text is invalid")
    return RoomEvent(
        event_id=row.event_id,
        session_id=row.session_id,
        sequence=row.sequence,
        source_type=RoomEventSource(row.source_type),
        source_id=row.source_id or None,
        created_at_ms=row.occurred_at_ms,
        text=text,
        payload=payload,
    )


def _thaw(value: JsonValue | Mapping[str, JsonValue]) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


_PAYLOAD_MODELS: dict[RoomEventSource, type[_PayloadModel]] = {
    RoomEventSource.USER_TEXT: _UserTextPayload,
    RoomEventSource.USER_VOICE: _UserVoicePayload,
    RoomEventSource.AUDIENCE_BARRAGE: _AudienceBarragePayload,
    RoomEventSource.SCREEN_OBSERVATION: _ScreenObservationPayload,
    RoomEventSource.SYSTEM_EVENT: _SystemEventPayload,
}


def _validate_persisted_payload(
    source_type: RoomEventSource,
    payload: Mapping[str, JsonValue],
) -> None:
    _reject_embedded_media(payload)
    _PAYLOAD_MODELS[source_type].model_validate(_thaw(payload))


def _reject_embedded_media(value: JsonValue | Mapping[str, JsonValue]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.casefold()
            if "blob" in normalized or "pixel" in normalized:
                raise ValueError("raw media must not be persisted in room events")
            _reject_embedded_media(item)
    elif isinstance(value, tuple):
        for item in value:
            _reject_embedded_media(item)
    elif (
        isinstance(value, str)
        and value.lstrip().casefold().startswith("data:")
    ):
        raise ValueError("raw media must not be persisted in room events")


__all__ = [
    "PersistentRuntimeRoomEventStore",
    "RoomEventRecoveryReader",
    "RoomEventPersistenceUnavailableError",
    "persisted_room_event",
    "restore_room_event",
]
