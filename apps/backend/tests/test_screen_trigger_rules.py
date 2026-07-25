from collections.abc import Awaitable, Callable

import pytest

from advx_backend.application.ingest_service import IngestService
from advx_backend.application.ports.ingest import FrameInput, TextInput
from advx_backend.application.viewer_runtime_coordinator import ViewerRuntimeCoordinator
from advx_backend.domain.observation import FrameRef, Observation
from advx_backend.domain.observation_wave import ObservationTrigger
from advx_backend.domain.room import RoomEvent


class _Clock:
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def now_ms(self) -> int:
        return self.value


class _SessionTasks:
    async def accepts_results(self, session_id: str) -> bool:
        return session_id == "session"


class _FrameStore:
    def __init__(self) -> None:
        self.frames: list[FrameRef] = []

    async def store(self, input: FrameInput) -> FrameRef:
        frame = FrameRef(
            frame_id=f"frame-{len(self.frames) + 1}",
            created_at_ms=input.captured_at_ms,
            mime_type=input.mime_type,
            data_ref=f"frame:{input.input_id}",
        )
        self.frames.append(frame)
        return frame


class _Room:
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.events: list[RoomEvent] = []

    async def append_event(self, session_id: str, **values: object) -> RoomEvent:
        event = RoomEvent(
            event_id=f"event-{len(self.events) + 1}",
            session_id=session_id,
            sequence=len(self.events) + 1,
            source_type=values["source_type"],
            created_at_ms=self._clock.now_ms(),
            source_id=values.get("source_id"),
            text=values.get("text"),
            payload=values.get("payload", {}),
        )
        self.events.append(event)
        return event


class _ContextBuilder:
    def __init__(self, *, clock: _Clock, room: _Room) -> None:
        self._clock = clock
        self._room = room
        self.frames: list[FrameRef] = []
        self.calls: list[dict[str, object]] = []

    async def append_frame_ref(self, session_id: str, frame: FrameRef) -> FrameRef:
        assert session_id == "session"
        self.frames.append(frame)
        return frame

    async def build(self, session_id: str, **values: object) -> Observation:
        self.calls.append(values)
        if values.get("user_context") is None:
            values.pop("user_context", None)
        return Observation(
            session_id=session_id,
            observation_id=f"observation-{len(self.calls)}",
            created_at_ms=self._clock.now_ms(),
            frames=tuple(self.frames),
            room_events=tuple(self._room.events),
            **values,
        )


class _Scheduler:
    def __init__(self) -> None:
        self.observations: list[Observation] = []

    async def submit(self, observation: Observation) -> None:
        self.observations.append(observation)

    async def cancel_session(self, session_id: str) -> None:
        del session_id


def _service(
    clock: _Clock,
    *,
    screen_trigger_settings: Callable[[str], Awaitable[tuple[float, int]]] | None = None,
) -> tuple[IngestService, _Scheduler]:
    room = _Room(clock)
    scheduler = _Scheduler()
    service = IngestService(
        room_service=room,
        context_builder=_ContextBuilder(clock=clock, room=room),
        frame_store=_FrameStore(),
        asr_provider=object(),
        scheduler=scheduler,
        session_tasks=_SessionTasks(),
        clock=clock,
        screen_trigger_settings=screen_trigger_settings,
    )
    service._active_session_id = "session"
    return service, scheduler


async def _submit_frame(
    service: IngestService,
    *,
    input_id: str,
    captured_at_ms: int,
    change_score: float | None,
) -> None:
    await service.submit_frame(
        FrameInput(
            session_id="session",
            input_id=input_id,
            captured_at_ms=captured_at_ms,
            mime_type="image/jpeg",
            body=b"frame",
            change_score=change_score,
        )
    )


@pytest.mark.asyncio
async def test_only_a_change_at_or_above_the_threshold_creates_a_screen_wave() -> None:
    clock = _Clock()
    service, scheduler = _service(clock)

    await _submit_frame(
        service,
        input_id="below-threshold",
        captured_at_ms=0,
        change_score=0.199,
    )
    await _submit_frame(
        service,
        input_id="at-threshold",
        captured_at_ms=1,
        change_score=0.2,
    )

    assert len(scheduler.observations) == 1
    assert scheduler.observations[0].trigger_frame_ids == ("frame-2",)


@pytest.mark.asyncio
async def test_any_trigger_starts_the_ten_second_screen_cooldown() -> None:
    clock = _Clock(value=1_000)
    service, scheduler = _service(clock)

    await service.submit_text(
        TextInput(
            session_id="session",
            input_id="text",
            created_at_ms=clock.now_ms(),
            text="主播发言",
        )
    )
    clock.value = 10_999
    await _submit_frame(
        service,
        input_id="too-soon",
        captured_at_ms=clock.now_ms(),
        change_score=0.2,
    )
    clock.value = 11_000
    await _submit_frame(
        service,
        input_id="cold-again",
        captured_at_ms=clock.now_ms(),
        change_score=0.2,
    )

    assert len(scheduler.observations) == 2
    assert scheduler.observations[-1].trigger_frame_ids == ("frame-2",)


@pytest.mark.asyncio
async def test_runtime_screen_settings_control_the_threshold_and_cooldown() -> None:
    clock = _Clock()
    settings_calls: list[str] = []

    async def screen_trigger_settings(session_id: str) -> tuple[float, int]:
        settings_calls.append(session_id)
        return 0.25, 500

    service, scheduler = _service(
        clock,
        screen_trigger_settings=screen_trigger_settings,
    )

    await _submit_frame(
        service,
        input_id="below-runtime-threshold",
        captured_at_ms=0,
        change_score=0.249,
    )
    await _submit_frame(
        service,
        input_id="at-runtime-threshold",
        captured_at_ms=0,
        change_score=0.25,
    )
    clock.value = 499
    await _submit_frame(
        service,
        input_id="before-runtime-cooldown",
        captured_at_ms=499,
        change_score=0.25,
    )
    clock.value = 500
    await _submit_frame(
        service,
        input_id="after-runtime-cooldown",
        captured_at_ms=500,
        change_score=0.25,
    )

    assert settings_calls == ["session", "session", "session", "session"]
    assert [item.trigger_frame_ids for item in scheduler.observations] == [
        ("frame-2",),
        ("frame-4",),
    ]


def test_a_trigger_frame_is_a_screen_change_for_the_viewer_runtime() -> None:
    frame = FrameRef(
        frame_id="frame-1",
        created_at_ms=0,
        mime_type="image/jpeg",
        data_ref="frame:1",
    )
    observation = Observation(
        session_id="session",
        observation_id="observation",
        created_at_ms=0,
        frames=(frame,),
        trigger_frame_ids=(frame.frame_id,),
    )

    assert ViewerRuntimeCoordinator._triggers(observation) == [ObservationTrigger.SCREEN_CHANGE]
