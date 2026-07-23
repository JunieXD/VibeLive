import asyncio
from dataclasses import FrozenInstanceError

import pytest

from advx_backend.application.context_builder import ContextBuilder
from advx_backend.application.room_service import RoomService, RoomSessionNotActiveError
from advx_backend.domain.room import RoomEvent


class MutableClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


class SequenceIdGenerator:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value}"


class PausingRoomService(RoomService):
    def __init__(self, *, clock: MutableClock) -> None:
        super().__init__(
            clock=clock,
            id_generator=SequenceIdGenerator("event"),
            event_capacity=10,
            event_ttl_ms=10_000,
        )
        self.pause_reads = False
        self.read_started = asyncio.Event()
        self.resume_reads = asyncio.Event()

    async def read_events(
        self,
        session_id: str,
        *,
        max_count: int | None = None,
    ) -> tuple[RoomEvent, ...]:
        events = await super().read_events(session_id, max_count=max_count)
        if self.pause_reads:
            self.read_started.set()
            await self.resume_reads.wait()
        return events


def create_pipeline(
    clock: MutableClock,
    *,
    frame_capacity: int = 4,
    frame_ttl_ms: int = 1_000,
    max_frames: int = 2,
    max_events: int = 2,
) -> tuple[RoomService, ContextBuilder]:
    room = RoomService(
        clock=clock,
        id_generator=SequenceIdGenerator("event"),
        event_capacity=10,
        event_ttl_ms=10_000,
    )
    builder = ContextBuilder(
        room_service=room,
        clock=clock,
        id_generator=SequenceIdGenerator("context"),
        frame_capacity=frame_capacity,
        frame_ttl_ms=frame_ttl_ms,
        max_frames_per_observation=max_frames,
        max_events_per_observation=max_events,
    )
    return room, builder


@pytest.mark.asyncio
async def test_observation_does_not_change_with_later_buffer_mutations() -> None:
    clock = MutableClock()
    room, builder = create_pipeline(clock)
    await builder.start_session("session-1")
    payload = {"nested": {"values": [1, 2]}}
    await room.append_event(
        "session-1",
        source_type="user_text",
        text="first",
        payload=payload,
    )
    await builder.append_frame(
        "session-1",
        mime_type="image/webp",
        data_ref="memory://frame-1",
    )
    context = {"topic": "demo"}

    observation = await builder.build("session-1", user_context=context)

    context["topic"] = "changed"
    payload["nested"]["values"].append(3)
    await room.append_event("session-1", source_type="user_text", text="second")
    await builder.append_frame(
        "session-1",
        mime_type="image/webp",
        data_ref="memory://frame-2",
    )

    assert [event.text for event in observation.room_events] == ["first"]
    assert [frame.data_ref for frame in observation.frames] == ["memory://frame-1"]
    assert observation.user_context == {"topic": "demo"}
    assert observation.room_events[0].payload["nested"]["values"] == (1, 2)
    with pytest.raises(FrozenInstanceError):
        observation.created_at_ms = 0
    with pytest.raises(TypeError):
        observation.room_events[0].payload["new"] = "value"


@pytest.mark.asyncio
async def test_context_builder_applies_frame_capacity_ttl_and_sample_limits() -> None:
    clock = MutableClock()
    room, builder = create_pipeline(
        clock,
        frame_capacity=3,
        frame_ttl_ms=100,
        max_frames=2,
        max_events=2,
    )
    await builder.start_session("session-1")

    for index in range(3):
        await room.append_event("session-1", source_type="user_text", text=f"event-{index}")
        await builder.append_frame(
            "session-1",
            mime_type="image/webp",
            data_ref=f"memory://frame-{index}",
        )
        clock.advance(20)

    sampled = await builder.build("session-1")
    assert [event.text for event in sampled.room_events] == ["event-1", "event-2"]
    assert [frame.data_ref for frame in sampled.frames] == [
        "memory://frame-1",
        "memory://frame-2",
    ]

    clock.advance(80)
    expired = await builder.build("session-1")
    assert expired.frames == ()


@pytest.mark.asyncio
async def test_context_builder_clears_replaced_and_stopped_sessions() -> None:
    clock = MutableClock()
    room, builder = create_pipeline(clock)
    await builder.start_session("session-old")
    await room.append_event("session-old", source_type="user_text", text="old")
    await builder.append_frame(
        "session-old",
        mime_type="image/webp",
        data_ref="memory://old-frame",
    )

    await builder.replace_session("session-new")
    current = await builder.build("session-new")

    assert current.room_events == ()
    assert current.frames == ()
    with pytest.raises(RoomSessionNotActiveError):
        await builder.build("session-old")
    with pytest.raises(RoomSessionNotActiveError):
        await builder.append_frame(
            "session-old",
            mime_type="image/webp",
            data_ref="memory://late-old-frame",
        )

    await builder.append_frame(
        "session-new",
        mime_type="image/webp",
        data_ref="memory://new-frame",
    )
    await builder.stop_session("session-new")
    with pytest.raises(RoomSessionNotActiveError):
        await builder.build("session-new")

    await builder.start_session("session-after-stop")
    after_stop = await builder.build("session-after-stop")
    assert after_stop.room_events == ()
    assert after_stop.frames == ()


@pytest.mark.asyncio
async def test_in_flight_old_build_cannot_clear_new_session_frames() -> None:
    clock = MutableClock()
    room = PausingRoomService(clock=clock)
    builder = ContextBuilder(
        room_service=room,
        clock=clock,
        id_generator=SequenceIdGenerator("context"),
        frame_capacity=4,
        frame_ttl_ms=1_000,
        max_frames_per_observation=2,
        max_events_per_observation=2,
    )
    await builder.start_session("session-old")
    await room.append_event("session-old", source_type="user_text", text="old")

    room.pause_reads = True
    stale_build = asyncio.create_task(builder.build("session-old"))
    await room.read_started.wait()

    await builder.replace_session("session-new")
    await builder.append_frame(
        "session-new",
        mime_type="image/webp",
        data_ref="memory://new-frame",
    )
    room.resume_reads.set()

    with pytest.raises(RoomSessionNotActiveError):
        await stale_build

    room.pause_reads = False
    current = await builder.build("session-new")
    assert [frame.data_ref for frame in current.frames] == ["memory://new-frame"]
