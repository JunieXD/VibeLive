import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from advx_backend.application.ports.asr import AudioSource
from advx_backend.application.room_event_persistence import (
    PersistentRuntimeRoomEventStore,
    RoomEventPersistenceUnavailableError,
    persisted_room_event,
    restore_room_event,
)
from advx_backend.application.room_service import RoomService
from advx_backend.application.runtime_session_service import RuntimeSessionService
from advx_backend.application.runtime_state import RuntimeStateStore
from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.room import RoomEvent, RoomEventSource
from advx_backend.infrastructure.persistence.sqlite import DatabaseConfig, SQLiteDatabase
from advx_backend.infrastructure.persistence.sqlite.models import (
    RoomEventRow,
    SessionRecordRow,
)


class IncrementingClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now_ms(self) -> int:
        self.value += 1
        return self.value


class SequenceIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    active = SQLiteDatabase(DatabaseConfig(data_directory=tmp_path))
    await active.start()
    try:
        yield active
    finally:
        await active.close()


def runtime_spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash=f"{1:064x}",
        display_name="Persona",
        role="viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.2,
        cooldown_ms=0,
    )
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1",
        revision=1,
        persona_counts={persona.persona_id: 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=0, maximum=1),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


def runtime_harness(
    database: SQLiteDatabase,
) -> tuple[
    RuntimeSessionService,
    RoomService,
    PersistentRuntimeRoomEventStore,
    RuntimeStateStore,
]:
    clock = IncrementingClock()
    runtime_state = RuntimeStateStore()
    event_store = PersistentRuntimeRoomEventStore(
        session_factory=database.session_factory,
        runtime_state=runtime_state,
        max_events=8,
        event_ttl_ms=10_000,
    )
    room_service = RoomService(
        clock=clock,
        id_generator=SequenceIds("event"),
        event_capacity=8,
        event_ttl_ms=10_000,
        event_persister=event_store.persist,
    )
    runtime_service = RuntimeSessionService(
        session_factory=database.session_factory,
        viewer_pool=ViewerPoolService(id_generator=SequenceIds("viewer")),
        clock=clock,
        id_generator=SequenceIds("session"),
        runtime_state=runtime_state,
        room_service=room_service,
        room_event_recovery=event_store,
        app_version="test",
    )
    return runtime_service, room_service, event_store, runtime_state


async def start_runtime(
    runtime_service: RuntimeSessionService,
    room_service: RoomService,
):
    spec = runtime_spec()
    snapshot = await runtime_service.start(
        RuntimeSessionStartRequest(
            client_request_id="request-1",
            canonical_runtime_spec=spec,
            client_config_hash=spec.config_hash(),
        )
    )
    await room_service.start_session(snapshot.session_id)
    return snapshot


@pytest.mark.asyncio
async def test_user_text_event_is_durable_and_recoverable_for_active_runtime(
    database: SQLiteDatabase,
) -> None:
    runtime_service, room_service, event_store, _ = runtime_harness(database)
    started = await start_runtime(runtime_service, room_service)

    event = await room_service.append_event(
        started.session_id,
        source_type=RoomEventSource.USER_TEXT,
        source_id="host",
        text="  hold this angle  ",
        payload={"input_id": "text-1"},
    )

    async with database.session_factory() as session:
        row = await session.scalar(select(RoomEventRow))
    assert row is not None
    assert row.content_hash == hashlib.sha256(row.content_json.encode()).hexdigest()
    assert json.loads(row.content_json) == {
        "audience_epoch": 1,
        "event_id": event.event_id,
        "occurred_at_ms": event.created_at_ms,
        "payload": {"input_id": "text-1"},
        "room_id": "room-1",
        "schema_version": 1,
        "sequence": 1,
        "session_id": started.session_id,
        "source_id": "host",
        "source_type": "user_text",
        "text": "  hold this angle  ",
    }
    assert await event_store.load_for_recovery(
        room_id="room-1",
        session_id=started.session_id,
        maximum_audience_epoch=1,
    ) == (event,)


@pytest.mark.asyncio
async def test_microphone_voice_is_durable_and_recoverable(
    database: SQLiteDatabase,
) -> None:
    runtime_service, room_service, event_store, _ = runtime_harness(database)
    started = await start_runtime(runtime_service, room_service)

    event = await room_service.append_event(
        started.session_id,
        source_type=RoomEventSource.USER_VOICE,
        source_id="host",
        text="voice",
        payload={
            "audio_source": AudioSource.MICROPHONE.value,
            "final": True,
            "started_at_ms": 100,
            "ended_at_ms": 200,
            "utterance_id": "microphone-1",
            "revision": 1,
        },
    )

    async with database.session_factory() as session:
        row = await session.scalar(
            select(RoomEventRow).where(RoomEventRow.event_id == event.event_id)
    )
    assert row is not None
    assert json.loads(row.content_json)["payload"]["audio_source"] == "microphone"
    assert await event_store.load_for_recovery(
        room_id="room-1",
        session_id=started.session_id,
        maximum_audience_epoch=1,
    ) == (event,)


@pytest.mark.asyncio
async def test_system_audio_transcript_is_durable_context_not_user_voice(
    database: SQLiteDatabase,
) -> None:
    runtime_service, room_service, event_store, _ = runtime_harness(database)
    started = await start_runtime(runtime_service, room_service)

    event = await room_service.append_event(
        started.session_id,
        source_type=RoomEventSource.SYSTEM_EVENT,
        source_id="system-audio",
        text="video dialogue",
        payload={
            "event": "system_audio_transcript",
            "audio_source": AudioSource.SYSTEM_AUDIO.value,
            "final": True,
            "started_at_ms": 100,
            "ended_at_ms": 200,
            "utterance_id": "system-audio-1",
            "revision": 1,
        },
    )

    async with database.session_factory() as session:
        row = await session.scalar(
            select(RoomEventRow).where(RoomEventRow.event_id == event.event_id)
        )
    assert row is not None
    content = json.loads(row.content_json)
    assert content["source_type"] == "system_event"
    assert content["source_id"] == "system-audio"
    assert await event_store.load_for_recovery(
        room_id="room-1",
        session_id=started.session_id,
        maximum_audience_epoch=1,
    ) == (event,)


def test_audience_barrage_persistence_accepts_intent_and_target() -> None:
    event = RoomEvent(
        event_id="event-1",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.AUDIENCE_BARRAGE,
        source_id="barrage-1",
        created_at_ms=1_000,
        text="说得对",
        payload={
            "barrage_id": "barrage-1",
            "audience_epoch": 1,
            "observation_id": "observation-1",
            "generation_request_id": "request-1",
            "viewer_instance_id": "viewer-1",
            "persona_id": "persona-1",
            "display_name": "Viewer",
            "viewer_sequence": 1,
            "reaction_type": "reply",
            "intent": "reply_to_viewer",
            "target": {"kind": "viewer", "viewer_instance_id": "viewer-2"},
            "evidence_refs": [{"source": "event", "event_id": "event-0"}],
            "expires_at_ms": 2_000,
        },
    )

    persisted = persisted_room_event(event, room_id="room-1", audience_epoch=1)

    assert restore_room_event(
        persisted,
        expected_room_id="room-1",
        expected_session_id="session-1",
        maximum_audience_epoch=1,
    ) == event


def test_room_event_recovery_rejects_tampered_content_hash() -> None:
    event = RoomEvent(
        event_id="event-1",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.USER_TEXT,
        source_id="host",
        created_at_ms=1_000,
        text="hold",
        payload={"input_id": "text-1"},
    )
    row = replace(
        persisted_room_event(event, room_id="room-1", audience_epoch=1),
        content_hash="0" * 64,
    )

    with pytest.raises(ValueError, match="content hash does not match"):
        restore_room_event(
            row,
            expected_room_id="room-1",
            expected_session_id="session-1",
            maximum_audience_epoch=1,
        )


@pytest.mark.parametrize(
    "event",
    [
        RoomEvent(
            event_id="event-large-text",
            session_id="session-1",
            sequence=1,
            source_type=RoomEventSource.USER_TEXT,
            created_at_ms=1,
            text="x" * 4_001,
        ),
        RoomEvent(
            event_id="event-raw-media",
            session_id="session-1",
            sequence=1,
            source_type=RoomEventSource.SCREEN_OBSERVATION,
            created_at_ms=1,
            payload={"image_bytes": "secret"},
        ),
        RoomEvent(
            event_id="event-nested-data-url",
            session_id="session-1",
            sequence=1,
            source_type=RoomEventSource.SCREEN_OBSERVATION,
            created_at_ms=1,
            payload={"nested": {"value": "data:image/png;base64,abc"}},
        ),
        RoomEvent(
            event_id="event-viewer-sequence-type",
            session_id="session-1",
            sequence=1,
            source_type=RoomEventSource.AUDIENCE_BARRAGE,
            created_at_ms=1,
            payload={"viewer_sequence": "1"},
        ),
    ],
)
def test_room_event_persistence_rejects_oversize_and_raw_media(
    event: RoomEvent,
) -> None:
    with pytest.raises(ValueError, match="limit|raw media|unsupported|validation"):
        persisted_room_event(event, room_id="room-1", audience_epoch=1)


@pytest.mark.asyncio
async def test_missing_runtime_snapshot_fails_instead_of_succeeding_in_memory_only(
    database: SQLiteDatabase,
) -> None:
    store = PersistentRuntimeRoomEventStore(
        session_factory=database.session_factory,
        runtime_state=RuntimeStateStore(),
        max_events=8,
        event_ttl_ms=10_000,
    )
    event = RoomEvent(
        event_id="event-no-snapshot",
        session_id="missing-session",
        sequence=1,
        source_type=RoomEventSource.USER_TEXT,
        created_at_ms=1,
        payload={"input_id": "input-1"},
    )

    with pytest.raises(
        RoomEventPersistenceUnavailableError,
        match="committed runtime snapshot",
    ) as captured:
        await store.persist(event)
    assert captured.value.code == "runtime_persistence_unavailable"
@pytest.mark.parametrize(
    (
        "expected_room_id",
        "expected_session_id",
        "audience_epoch",
        "maximum_audience_epoch",
    ),
    [
        ("other-room", "session-1", 1, 1),
        ("room-1", "other-session", 1, 1),
        ("room-1", "session-1", 2, 1),
    ],
)
def test_room_event_recovery_rejects_mismatched_scope(
    expected_room_id: str,
    expected_session_id: str,
    audience_epoch: int,
    maximum_audience_epoch: int,
) -> None:
    event = RoomEvent(
        event_id="event-1",
        session_id="session-1",
        sequence=1,
        source_type=RoomEventSource.USER_TEXT,
        source_id="host",
        created_at_ms=1_000,
        text="hold",
    )
    row = persisted_room_event(
        event,
        room_id="room-1",
        audience_epoch=audience_epoch,
    )

    with pytest.raises(ValueError, match="scope is invalid"):
        restore_room_event(
            row,
            expected_room_id=expected_room_id,
            expected_session_id=expected_session_id,
            maximum_audience_epoch=maximum_audience_epoch,
        )


@pytest.mark.asyncio
async def test_runtime_recovery_restores_public_events_and_next_sequence(
    database: SQLiteDatabase,
) -> None:
    runtime_service, room_service, _, runtime_state = runtime_harness(database)
    started = await start_runtime(runtime_service, room_service)
    first = await room_service.append_event(
        started.session_id,
        source_type=RoomEventSource.USER_TEXT,
        source_id="host",
        text="first",
    )
    await runtime_state.stop(started.session_id)
    await room_service.stop_session(started.session_id)
    await room_service.start_session(started.session_id)
    async with database.session_factory() as session:
        await session.execute(
            update(SessionRecordRow)
            .where(SessionRecordRow.session_id == started.session_id)
            .values(state="stopped", ended_at_ms=2_000, outcome="interrupted")
        )
        await session.commit()

    await runtime_service.recover(started.session_id)

    assert await room_service.read_events(started.session_id) == (first,)
    second = await room_service.append_event(
        started.session_id,
        source_type=RoomEventSource.USER_TEXT,
        source_id="host",
        text="second",
    )
    assert second.sequence == 2
