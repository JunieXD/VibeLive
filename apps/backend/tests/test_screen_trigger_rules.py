import asyncio
from collections.abc import Awaitable, Callable

import pytest

from advx_backend.application.ingest_service import IngestService
from advx_backend.application.ports.ingest import FrameInput, TextInput
from advx_backend.application.reaction_scheduler import LatestWinsReactionScheduler
from advx_backend.application.viewer_runtime_coordinator import ViewerRuntimeCoordinator
from advx_backend.domain.observation import FrameRef, Observation
from advx_backend.domain.observation_wave import ObservationTrigger
from advx_backend.domain.room import RoomEvent, RoomEventSource


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
        self.submitted = asyncio.Event()

    async def submit(self, observation: Observation) -> None:
        self.observations.append(observation)
        self.submitted.set()

    async def cancel_session(self, session_id: str) -> None:
        del session_id


def _service(
    clock: _Clock,
    *,
    screen_trigger_settings: Callable[[str], Awaitable[tuple[float, int]]] | None = None,
    window_batch_schedule: (
        Callable[[str], Awaitable[tuple[bool, int]]] | None
    ) = None,
    window_batch_mode_poll_ms: int = 250,
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
        window_batch_schedule=window_batch_schedule,
        window_batch_mode_poll_ms=window_batch_mode_poll_ms,
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
async def test_any_trigger_starts_the_five_second_screen_cooldown() -> None:
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
    clock.value = 5_999
    await _submit_frame(
        service,
        input_id="too-soon",
        captured_at_ms=clock.now_ms(),
        change_score=0.2,
    )
    clock.value = 6_000
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


@pytest.mark.asyncio
async def test_window_batch_aggregates_inputs_until_one_fixed_tick() -> None:
    clock = _Clock()

    async def window_batch_schedule(session_id: str) -> tuple[bool, int]:
        assert session_id == "session"
        return True, 50

    service, scheduler = _service(
        clock,
        window_batch_schedule=window_batch_schedule,
        window_batch_mode_poll_ms=5,
    )
    timer = asyncio.create_task(service._run_window_batch_timer("session"))
    try:
        await service.submit_text(
            TextInput(
                session_id="session",
                input_id="text-1",
                created_at_ms=0,
                text="第一句",
                target_viewer_id="viewer-1",
            )
        )
        await service.submit_text(
            TextInput(
                session_id="session",
                input_id="text-2",
                created_at_ms=1,
                text="第二句",
            )
        )
        await _submit_frame(
            service,
            input_id="screen-only",
            captured_at_ms=2,
            change_score=1.0,
        )

        assert scheduler.observations == []
        await asyncio.wait_for(scheduler.submitted.wait(), timeout=0.3)

        assert len(scheduler.observations) == 1
        observation = scheduler.observations[0]
        assert len(observation.room_events) == 2
        assert len(observation.frames) == 1
        assert observation.trigger_event_ids == ("event-1", "event-2")
        assert observation.trigger_frame_ids == ("frame-1",)
        assert observation.target_viewer_id == "viewer-1"
        assert observation.user_context == {"window_batch": "true"}
        assert ViewerRuntimeCoordinator._triggers(observation) == [
            ObservationTrigger.USER_TEXT,
            ObservationTrigger.SCREEN_CHANGE,
        ]
        assert LatestWinsReactionScheduler._priority(observation) == 3
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


@pytest.mark.asyncio
async def test_window_batch_uses_ambient_only_when_the_tick_has_no_new_input() -> None:
    clock = _Clock()

    async def window_batch_schedule(session_id: str) -> tuple[bool, int]:
        assert session_id == "session"
        return True, 20

    service, scheduler = _service(
        clock,
        window_batch_schedule=window_batch_schedule,
        window_batch_mode_poll_ms=5,
    )
    timer = asyncio.create_task(service._run_window_batch_timer("session"))
    try:
        await asyncio.wait_for(scheduler.submitted.wait(), timeout=0.2)

        observation = scheduler.observations[0]
        assert observation.trigger_event_ids == ()
        assert observation.trigger_frame_ids == ()
        assert observation.user_context == {
            "ambient": "true",
            "window_batch": "true",
        }
        assert ViewerRuntimeCoordinator._triggers(observation) == [
            ObservationTrigger.AMBIENT_TICK
        ]
        assert LatestWinsReactionScheduler._priority(observation) == 1
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


@pytest.mark.asyncio
async def test_window_batch_preserves_microphone_and_system_asr_trigger_delta() -> None:
    clock = _Clock()

    async def window_batch_schedule(session_id: str) -> tuple[bool, int]:
        assert session_id == "session"
        return True, 30

    service, scheduler = _service(
        clock,
        window_batch_schedule=window_batch_schedule,
        window_batch_mode_poll_ms=5,
    )
    room = service._room_service
    microphone = await room.append_event(
        "session",
        source_type=RoomEventSource.USER_VOICE,
        source_id="host",
        text="麦克风语音",
        payload={"final": True},
    )
    system_audio = await room.append_event(
        "session",
        source_type=RoomEventSource.SYSTEM_EVENT,
        source_id="system-audio",
        text="视频语音",
        payload={"event": "system_audio_transcript", "final": True},
    )
    await service._schedule_observation(
        "session",
        trigger_event_ids=(microphone.event_id,),
    )
    await service._schedule_observation(
        "session",
        trigger_event_ids=(system_audio.event_id,),
    )

    timer = asyncio.create_task(service._run_window_batch_timer("session"))
    try:
        await asyncio.wait_for(scheduler.submitted.wait(), timeout=0.2)

        observation = scheduler.observations[0]
        assert observation.trigger_event_ids == (
            microphone.event_id,
            system_audio.event_id,
        )
        assert observation.user_context == {"window_batch": "true"}
        assert ViewerRuntimeCoordinator._triggers(observation) == [
            ObservationTrigger.FINAL_VOICE,
            ObservationTrigger.SYSTEM_AUDIO,
        ]
        assert LatestWinsReactionScheduler._priority(observation) == 3
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


@pytest.mark.asyncio
async def test_window_batch_screen_only_tick_keeps_screen_priority() -> None:
    clock = _Clock()

    async def window_batch_schedule(session_id: str) -> tuple[bool, int]:
        assert session_id == "session"
        return True, 30

    service, scheduler = _service(
        clock,
        window_batch_schedule=window_batch_schedule,
        window_batch_mode_poll_ms=5,
    )
    await _submit_frame(
        service,
        input_id="screen-only",
        captured_at_ms=0,
        change_score=1.0,
    )

    timer = asyncio.create_task(service._run_window_batch_timer("session"))
    try:
        await asyncio.wait_for(scheduler.submitted.wait(), timeout=0.2)

        observation = scheduler.observations[0]
        assert observation.trigger_event_ids == ()
        assert observation.trigger_frame_ids == ("frame-1",)
        assert observation.user_context == {"window_batch": "true"}
        assert ViewerRuntimeCoordinator._triggers(observation) == [
            ObservationTrigger.SCREEN_CHANGE
        ]
        assert LatestWinsReactionScheduler._priority(observation) == 2
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


@pytest.mark.asyncio
async def test_window_batch_ticker_follows_live_mode_switches() -> None:
    clock = _Clock()
    state = {"enabled": False}

    async def window_batch_schedule(session_id: str) -> tuple[bool, int]:
        assert session_id == "session"
        return state["enabled"], 20

    service, scheduler = _service(
        clock,
        window_batch_schedule=window_batch_schedule,
        window_batch_mode_poll_ms=5,
    )
    timer = asyncio.create_task(service._run_window_batch_timer("session"))
    try:
        await asyncio.sleep(0.03)
        assert scheduler.observations == []

        state["enabled"] = True
        await asyncio.wait_for(scheduler.submitted.wait(), timeout=0.2)
        assert len(scheduler.observations) == 1

        scheduler.submitted.clear()
        state["enabled"] = False
        await asyncio.sleep(0.05)
        assert len(scheduler.observations) == 1

        state["enabled"] = True
        await asyncio.wait_for(scheduler.submitted.wait(), timeout=0.2)
        assert len(scheduler.observations) == 2
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)
