import pytest

from advx_backend.application.generation_policies import (
    AudienceSelectorConfig,
    DefaultAudienceSelector,
    DefaultGenerationInvocationPlanner,
    DefaultGenerationTrigger,
    GenerationInvocationPlannerConfig,
    GenerationTriggerConfig,
)
from advx_backend.application.ports.generation import AudienceSnapshot
from advx_backend.contracts.audience import AudienceMember
from advx_backend.contracts.events import RoomEvent, RoomEventSource
from advx_backend.contracts.generation import AudienceContext, FrameRef, Observation


class FakeClock:
    def __init__(self, now_ms: int) -> None:
        self.now_ms_value = now_ms

    def now_ms(self) -> int:
        return self.now_ms_value


def make_observation(
    *,
    observation_id: str = "observation-1",
    created_at_ms: int = 100,
    frames: list[FrameRef] | None = None,
    room_events: list[RoomEvent] | None = None,
) -> Observation:
    return Observation(
        session_id="session-1",
        observation_id=observation_id,
        created_at_ms=created_at_ms,
        frames=[] if frames is None else frames,
        room_events=[] if room_events is None else room_events,
    )


def room_event(
    event_id: str,
    source_type: RoomEventSource,
    *,
    text: str | None = None,
    payload: dict[str, object] | None = None,
) -> RoomEvent:
    return RoomEvent(
        event_id=event_id,
        session_id="session-1",
        source_type=source_type,
        created_at_ms=100,
        text=text,
        payload={} if payload is None else payload,
    )


def frame(frame_id: str) -> FrameRef:
    return FrameRef(
        frame_id=frame_id,
        created_at_ms=100,
        mime_type="image/jpeg",
        data_ref=f"memory://{frame_id}",
    )


def audience_context(audience_id: str, *, enabled: bool = True) -> AudienceContext:
    return AudienceContext(
        member=AudienceMember(
            audience_id=audience_id,
            display_name=f"Audience {audience_id}",
            enabled=enabled,
        )
    )


@pytest.mark.asyncio
async def test_trigger_rejects_empty_and_expired_observations() -> None:
    clock = FakeClock(100)
    trigger = DefaultGenerationTrigger(clock=clock)

    assert not await trigger.should_generate(observation=make_observation())

    clock.now_ms_value = 131
    expired = make_observation(
        observation_id="expired",
        created_at_ms=100,
        room_events=[room_event("message", RoomEventSource.USER_TEXT, text="hello")],
    )
    short_ttl_trigger = DefaultGenerationTrigger(
        clock=clock,
        config=GenerationTriggerConfig(observation_ttl_ms=30),
    )

    assert not await short_ttl_trigger.should_generate(observation=expired)


@pytest.mark.asyncio
async def test_trigger_prioritizes_user_input_and_applies_screen_rules() -> None:
    clock = FakeClock(100)
    trigger = DefaultGenerationTrigger(
        clock=clock,
        config=GenerationTriggerConfig(
            screen_cooldown_ms=100,
            trigger_on_screen_events=False,
            minimum_new_frames=2,
        ),
    )
    user_event = room_event("user-1", RoomEventSource.USER_TEXT, text="hello")

    first = make_observation(
        created_at_ms=100,
        frames=[frame("frame-1"), frame("frame-2")],
        room_events=[user_event],
    )
    assert await trigger.should_generate(observation=first)

    clock.now_ms_value = 101
    not_enough_new_frames = make_observation(
        observation_id="observation-2",
        created_at_ms=101,
        frames=[frame("frame-1"), frame("frame-2"), frame("frame-3")],
        room_events=[user_event],
    )
    assert not await trigger.should_generate(observation=not_enough_new_frames)

    clock.now_ms_value = 102
    first_screen_trigger = make_observation(
        observation_id="observation-3",
        created_at_ms=102,
        frames=[
            frame("frame-1"),
            frame("frame-2"),
            frame("frame-3"),
            frame("frame-4"),
            frame("frame-5"),
        ],
        room_events=[user_event],
    )
    assert await trigger.should_generate(observation=first_screen_trigger)

    clock.now_ms_value = 150
    blocked_by_cooldown = make_observation(
        observation_id="observation-4",
        created_at_ms=150,
        frames=[
            frame("frame-2"),
            frame("frame-3"),
            frame("frame-4"),
            frame("frame-5"),
            frame("frame-6"),
            frame("frame-7"),
        ],
        room_events=[user_event],
    )
    assert not await trigger.should_generate(observation=blocked_by_cooldown)

    clock.now_ms_value = 202
    allowed_after_cooldown = make_observation(
        observation_id="observation-5",
        created_at_ms=202,
        frames=[
            frame("frame-3"),
            frame("frame-4"),
            frame("frame-5"),
            frame("frame-6"),
            frame("frame-7"),
            frame("frame-8"),
            frame("frame-9"),
        ],
        room_events=[user_event],
    )
    assert await trigger.should_generate(observation=allowed_after_cooldown)


@pytest.mark.asyncio
async def test_trigger_accepts_final_voice_and_never_self_triggers_by_default() -> None:
    clock = FakeClock(100)
    final_voice = room_event(
        "voice-final",
        RoomEventSource.USER_VOICE,
        text="final transcript",
        payload={"final": True},
    )
    partial_voice = room_event(
        "voice-partial",
        RoomEventSource.USER_VOICE,
        text="partial transcript",
        payload={"final": False},
    )
    strict_trigger = DefaultGenerationTrigger(
        clock=clock,
        config=GenerationTriggerConfig(
            screen_triggers_enabled=False,
            require_final_voice_marker=True,
        ),
    )

    assert (
        await strict_trigger.should_generate(
            observation=make_observation(room_events=[partial_voice])
        )
        is False
    )
    assert await strict_trigger.should_generate(
        observation=make_observation(
            observation_id="final-voice",
            room_events=[partial_voice, final_voice],
        )
    )

    default_trigger = DefaultGenerationTrigger(
        clock=clock,
        config=GenerationTriggerConfig(screen_triggers_enabled=False),
    )
    barrage = room_event(
        "barrage-1",
        RoomEventSource.AUDIENCE_BARRAGE,
        text="model output",
    )
    assert not await default_trigger.should_generate(
        observation=make_observation(room_events=[barrage])
    )


@pytest.mark.asyncio
async def test_opted_in_barrage_trigger_has_a_consecutive_cap() -> None:
    clock = FakeClock(100)
    trigger = DefaultGenerationTrigger(
        clock=clock,
        config=GenerationTriggerConfig(
            screen_triggers_enabled=False,
            audience_barrage_cooldown_ms=0,
            max_consecutive_audience_barrage_triggers=1,
        ),
    )

    first_barrage = make_observation(
        room_events=[room_event("barrage-1", RoomEventSource.AUDIENCE_BARRAGE, text="first")]
    )
    assert await trigger.should_generate(observation=first_barrage)

    second_barrage = make_observation(
        observation_id="observation-2",
        created_at_ms=101,
        room_events=[
            room_event("barrage-1", RoomEventSource.AUDIENCE_BARRAGE, text="first"),
            room_event("barrage-2", RoomEventSource.AUDIENCE_BARRAGE, text="second"),
        ],
    )
    assert not await trigger.should_generate(observation=second_barrage)

    user_input = make_observation(
        observation_id="observation-3",
        created_at_ms=102,
        room_events=[
            room_event("barrage-2", RoomEventSource.AUDIENCE_BARRAGE, text="second"),
            room_event("user-1", RoomEventSource.USER_TEXT, text="host input"),
        ],
    )
    assert await trigger.should_generate(observation=user_input)

    third_barrage = make_observation(
        observation_id="observation-4",
        created_at_ms=103,
        room_events=[
            room_event("user-1", RoomEventSource.USER_TEXT, text="host input"),
            room_event("barrage-3", RoomEventSource.AUDIENCE_BARRAGE, text="third"),
        ],
    )
    assert await trigger.should_generate(observation=third_barrage)


@pytest.mark.asyncio
async def test_selector_uses_only_enabled_snapshot_members_in_stable_order() -> None:
    selector = DefaultAudienceSelector(config=AudienceSelectorConfig(max_candidates=2))
    snapshot = AudienceSnapshot(
        session_id="session-1",
        observation_id="observation-1",
        audiences=(
            audience_context("audience-2"),
            audience_context("audience-disabled", enabled=False),
            audience_context("audience-2"),
            audience_context("audience-1"),
            audience_context("audience-3"),
        ),
    )

    selected = await selector.select_candidates(
        observation=make_observation(),
        snapshot=snapshot,
    )

    assert selected == ("audience-2", "audience-1")


@pytest.mark.asyncio
async def test_planner_batches_unique_audiences_without_reordering() -> None:
    planner = DefaultGenerationInvocationPlanner(
        config=GenerationInvocationPlannerConfig(batch_size=2)
    )
    candidates = (
        audience_context("audience-3"),
        audience_context("audience-1"),
        audience_context("audience-3"),
        audience_context("audience-2"),
        audience_context("audience-4"),
        audience_context("audience-5"),
    )

    batches = await planner.plan_invocations(
        observation=make_observation(),
        candidates=candidates,
    )

    assert [batch.audience_ids for batch in batches] == [
        ("audience-3", "audience-1"),
        ("audience-2", "audience-4"),
        ("audience-5",),
    ]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: GenerationTriggerConfig(observation_ttl_ms=0), "observation_ttl_ms"),
        (lambda: GenerationTriggerConfig(max_tracked_sessions=0), "max_tracked_sessions"),
        (lambda: AudienceSelectorConfig(max_candidates=0), "max_candidates"),
        (lambda: GenerationInvocationPlannerConfig(batch_size=0), "batch_size"),
    ],
)
def test_policy_configs_validate_bounds(factory: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()
