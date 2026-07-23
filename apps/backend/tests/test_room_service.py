import asyncio

import pytest

from advx_backend.application.room_service import RoomService, RoomSessionNotActiveError
from advx_backend.domain.room import RoomEventSource


class MutableClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


class SequenceIdGenerator:
    def __init__(self, prefix: str = "event") -> None:
        self.prefix = prefix
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


def create_room(
    clock: MutableClock,
    *,
    capacity: int = 10,
    ttl_ms: int = 1_000,
) -> RoomService:
    return RoomService(
        clock=clock,
        id_generator=SequenceIdGenerator(),
        event_capacity=capacity,
        event_ttl_ms=ttl_ms,
    )


@pytest.mark.asyncio
async def test_room_events_receive_strictly_increasing_sequences() -> None:
    clock = MutableClock()
    room = create_room(clock)
    await room.start_session("session-1")

    events = [
        await room.append_event(
            "session-1",
            source_type=RoomEventSource.USER_TEXT,
            text=f"message-{index}",
        )
        for index in range(3)
    ]

    assert [event.sequence for event in events] == [1, 2, 3]
    assert await room.read_events("session-1") == tuple(events)


@pytest.mark.asyncio
async def test_room_buffer_evicts_oldest_event_on_capacity_overflow() -> None:
    clock = MutableClock()
    room = create_room(clock, capacity=2)
    await room.start_session("session-1")

    for index in range(3):
        await room.append_event(
            "session-1",
            source_type="user_text",
            text=f"message-{index}",
        )

    retained = await room.read_events("session-1")
    assert [event.sequence for event in retained] == [2, 3]


@pytest.mark.asyncio
async def test_room_buffer_evicts_events_at_ttl_boundary() -> None:
    clock = MutableClock()
    room = create_room(clock, ttl_ms=100)
    await room.start_session("session-1")
    await room.append_event("session-1", source_type="user_voice", text="hello")

    clock.advance(99)
    assert len(await room.read_events("session-1")) == 1

    clock.advance(1)
    assert await room.read_events("session-1") == ()


@pytest.mark.asyncio
async def test_replaced_session_rejects_old_reads_and_writes() -> None:
    clock = MutableClock()
    room = create_room(clock)
    await room.start_session("session-old")
    await room.append_event("session-old", source_type="user_text", text="old")

    await room.replace_session("session-new")

    with pytest.raises(RoomSessionNotActiveError):
        await room.append_event("session-old", source_type="user_text", text="late")
    with pytest.raises(RoomSessionNotActiveError):
        await room.read_events("session-old")

    assert await room.read_events("session-new") == ()
    first_new = await room.append_event("session-new", source_type="user_text", text="new")
    assert first_new.sequence == 1


@pytest.mark.asyncio
async def test_stopped_session_is_cleared_before_a_new_session_starts() -> None:
    clock = MutableClock()
    room = create_room(clock)
    await room.start_session("session-1")
    await room.append_event("session-1", source_type="system_event", text="started")

    await room.stop_session("session-1")

    with pytest.raises(RoomSessionNotActiveError):
        await room.read_events("session-1")

    await room.start_session("session-2")
    assert await room.read_events("session-2") == ()
    first_new = await room.append_event("session-2", source_type="system_event")
    assert first_new.sequence == 1


@pytest.mark.asyncio
async def test_concurrent_appends_have_unique_ordered_sequences() -> None:
    clock = MutableClock()
    room = create_room(clock, capacity=200)
    await room.start_session("session-1")

    appended = await asyncio.gather(
        *(
            room.append_event("session-1", source_type="user_text", text=str(index))
            for index in range(100)
        )
    )
    retained = await room.read_events("session-1")

    assert sorted(event.sequence for event in appended) == list(range(1, 101))
    assert [event.sequence for event in retained] == list(range(1, 101))
    assert len({event.sequence for event in appended}) == 100
