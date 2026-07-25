from types import SimpleNamespace

import pytest

from advx_backend.application.viewer_runtime_coordinator import ViewerRuntimeCoordinator
from advx_backend.contracts.viewer_runtime import RuntimeSettings
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation import Observation
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    FrameBundleSettings,
    ObservationTrigger,
    ObservationWave,
    ViewerVisualInputMode,
)
from advx_backend.domain.room import RoomEvent, RoomEventSource


def _event(
    sequence: int,
    source_type: RoomEventSource,
    *,
    created_at_ms: int,
    text: str | None = None,
    payload: dict[str, object] | None = None,
) -> RoomEvent:
    return RoomEvent(
        event_id=f"event-{sequence}",
        session_id="session",
        sequence=sequence,
        source_type=source_type,
        created_at_ms=created_at_ms,
        source_id="source",
        text=text or f"text-{sequence}",
        payload=payload or {},
    )


def test_public_context_is_fresh_bounded_and_keeps_forced_triggers() -> None:
    events = [
        _event(index, RoomEventSource.USER_TEXT, created_at_ms=index * 1_000, text="old-topic")
        for index in range(1, 101)
    ]
    sequence = 101
    for source_type, payload in (
        (RoomEventSource.USER_TEXT, {}),
        (RoomEventSource.SYSTEM_EVENT, {"event": "system_audio_transcript"}),
        (RoomEventSource.SCREEN_OBSERVATION, {}),
    ):
        for _ in range(20):
            events.append(
                _event(
                    sequence,
                    source_type,
                    created_at_ms=270_000 + sequence,
                    text="new-topic",
                    payload=payload,
                )
            )
            sequence += 1
    for _ in range(40):
        events.append(
            _event(
                sequence,
                RoomEventSource.AUDIENCE_BARRAGE,
                created_at_ms=290_000 + sequence,
            )
        )
        sequence += 1
    forced = events[9]
    observation = Observation(
        session_id="session",
        observation_id="observation",
        created_at_ms=300_000,
        room_events=tuple(events),
        trigger_event_ids=(forced.event_id,),
    )

    public_context, reply_context = ViewerRuntimeCoordinator._select_contexts(
        observation,
        RuntimeSettings(),
    )

    assert forced in public_context
    assert len(public_context) == 48
    assert [event.sequence for event in public_context] == sorted(
        event.sequence for event in public_context
    )
    assert sum(
        event.source_type in {RoomEventSource.USER_TEXT, RoomEventSource.USER_VOICE}
        for event in public_context
    ) == 16
    assert sum(
        event.payload.get("event") == "system_audio_transcript"
        for event in public_context
    ) == 16
    assert sum(
        event.source_type is RoomEventSource.SCREEN_OBSERVATION
        for event in public_context
    ) == 16
    assert all(
        event.source_type is not RoomEventSource.AUDIENCE_BARRAGE
        for event in public_context
    )
    assert all(event.text != "old-topic" for event in public_context if event is not forced)
    assert len(reply_context) == 8
    assert all(
        event.source_type is RoomEventSource.AUDIENCE_BARRAGE
        for event in reply_context
    )


def test_memory_and_working_event_ids_use_only_filtered_public_context() -> None:
    old = _event(1, RoomEventSource.USER_TEXT, created_at_ms=1_000)
    recent = _event(2, RoomEventSource.USER_TEXT, created_at_ms=300_000)
    observation = Observation(
        session_id="session",
        observation_id="observation",
        created_at_ms=300_000,
        room_events=(old, recent),
        trigger_event_ids=(recent.event_id,),
    )
    public_context, reply_context = ViewerRuntimeCoordinator._select_contexts(
        observation,
        RuntimeSettings(),
    )

    runtime = ViewerRuntimeCoordinator._freeze_runtime(
        SimpleNamespace(room=SimpleNamespace(room_id="room")),
        observation,
        RoomMemorySlice(room_id="room", memory_revision=0),
        public_context=public_context,
        reply_context=reply_context,
    )

    assert runtime.public_context_event_ids == (recent.event_id,)
    assert runtime.working_memory.event_ids == [recent.event_id]
    assert runtime.conversation_history_summary is None


def test_reply_context_is_bounded_and_includes_the_newest_events_parent() -> None:
    parent = _event(
        1,
        RoomEventSource.AUDIENCE_BARRAGE,
        created_at_ms=75_000,
    )
    recent = [
        _event(
            sequence,
            RoomEventSource.AUDIENCE_BARRAGE,
            created_at_ms=99_000 + sequence,
        )
        for sequence in range(2, 10)
    ]
    newest = _event(
        10,
        RoomEventSource.AUDIENCE_BARRAGE,
        created_at_ms=100_000,
        payload={
            "target": {
                "kind": "event",
                "viewer_instance_id": None,
                "event_id": parent.event_id,
            }
        },
    )
    observation = Observation(
        session_id="session",
        observation_id="observation",
        created_at_ms=100_000,
        room_events=(parent, *recent, newest),
    )

    public_context, reply_context = ViewerRuntimeCoordinator._select_contexts(
        observation,
        RuntimeSettings(),
    )
    coordinator = ViewerRuntimeCoordinator(runtime_state=object(), viewer_runtime=object())
    assessment = coordinator._independent_assessment(
        _wave(ObservationTrigger.AMBIENT_TICK),
        SimpleNamespace(pool=SimpleNamespace(viewers=[])),
        SimpleNamespace(
            reply_context_event_ids=tuple(event.event_id for event in reply_context)
        ),
    )

    assert public_context == ()
    assert len(reply_context) == 8
    assert parent in reply_context
    assert newest in reply_context
    assert assessment.replyable_event_ids == [
        event.event_id for event in reply_context
    ]


def test_reply_context_does_not_reintroduce_a_parent_outside_its_window() -> None:
    stale_parent = _event(
        1,
        RoomEventSource.AUDIENCE_BARRAGE,
        created_at_ms=1_000,
    )
    newest = _event(
        2,
        RoomEventSource.AUDIENCE_BARRAGE,
        created_at_ms=100_000,
        payload={
            "target": {
                "kind": "event",
                "viewer_instance_id": None,
                "event_id": stale_parent.event_id,
            }
        },
    )
    observation = Observation(
        session_id="session",
        observation_id="observation",
        created_at_ms=100_000,
        room_events=(stale_parent, newest),
    )

    _, reply_context = ViewerRuntimeCoordinator._select_contexts(
        observation,
        RuntimeSettings(),
    )

    assert reply_context == (newest,)


def _viewer(
    viewer_id: str,
    *,
    persona_id: str = "persona",
    last_spoke_at_ms: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        viewer_instance_id=viewer_id,
        persona_id=persona_id,
        private_state=SimpleNamespace(last_spoke_at_ms=last_spoke_at_ms),
        is_active=lambda: True,
        is_muted=lambda now_ms: False,
    )


def _wave(
    trigger: ObservationTrigger,
    *,
    target_viewer_id: str | None = None,
    target_persona_id: str | None = None,
) -> ObservationWave:
    return ObservationWave(
        room_id="room",
        session_id="session",
        audience_epoch=1,
        observation_id=f"observation-{trigger.value}",
        created_at_ms=100_000,
        deadline_at_ms=130_000,
        triggers=[trigger],
        target_viewer_id=target_viewer_id,
        target_persona_id=target_persona_id,
    )


def test_speaker_budgets_and_direct_targets_are_deterministic_and_fair() -> None:
    viewers = [
        _viewer(f"viewer-{index:02}", last_spoke_at_ms=None if index < 2 else index * 1_000)
        for index in range(10)
    ]
    viewers.append(_viewer("persona-old", persona_id="target", last_spoke_at_ms=20_000))
    viewers.append(_viewer("persona-fresh", persona_id="target", last_spoke_at_ms=None))
    committed = SimpleNamespace(
        pool=SimpleNamespace(viewers=viewers),
        spec=SimpleNamespace(settings=RuntimeSettings()),
    )
    coordinator = ViewerRuntimeCoordinator(runtime_state=object(), viewer_runtime=object())

    user = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.USER_TEXT),
        committed=committed,
    )
    screen = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.SCREEN_CHANGE),
        committed=committed,
    )
    system_audio = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.SYSTEM_AUDIO),
        committed=committed,
    )
    ambient = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.AMBIENT_TICK),
        committed=committed,
    )
    direct = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.USER_TEXT, target_viewer_id="viewer-09"),
        committed=committed,
    )
    persona = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.USER_TEXT, target_persona_id="target"),
        committed=committed,
    )

    assert len(user.selected_viewer_ids) == 6
    assert len(screen.selected_viewer_ids) == 4
    assert system_audio.selected_viewer_ids == ["persona-fresh", "viewer-00"]
    assert ambient.selected_viewer_ids == ["persona-fresh", "viewer-00"]
    assert direct.selected_viewer_ids == ["viewer-09"]
    assert persona.selected_viewer_ids == ["persona-fresh"]


def test_system_audio_transcript_is_a_real_observation_trigger() -> None:
    event = _event(
        1,
        RoomEventSource.SYSTEM_EVENT,
        created_at_ms=100_000,
        payload={"event": "system_audio_transcript"},
    )
    observation = Observation(
        session_id="session",
        observation_id="observation-system-audio",
        created_at_ms=100_000,
        room_events=(event,),
        trigger_event_ids=(event.event_id,),
    )

    assert ViewerRuntimeCoordinator._triggers(observation) == [
        ObservationTrigger.SYSTEM_AUDIO
    ]


@pytest.mark.asyncio
async def test_direct_frames_drop_stale_items_but_retain_trigger_frame() -> None:
    frames = [
        FrameBundleItem(
            frame_id=frame_id,
            frame_index=index,
            captured_at_ms=captured_at_ms,
            width=1280,
            height=720,
            encoding="jpeg",
            content_hash=f"{index + 1:064x}",
            data_ref=f"frame:{frame_id}",
        )
        for index, (frame_id, captured_at_ms) in enumerate(
            (("stale", 10_000), ("trigger", 20_000), ("fresh", 90_000))
        )
    ]
    wave = ObservationWave(
        room_id="room",
        session_id="session",
        audience_epoch=1,
        observation_id="observation",
        created_at_ms=100_000,
        deadline_at_ms=130_000,
        triggers=[ObservationTrigger.USER_TEXT],
        trigger_frame_ids=["trigger"],
        frame_bundle=FrameBundle(
            bundle_id="bundle",
            settings=FrameBundleSettings(frame_bundle_size=3),
            frames=frames,
        ),
    )
    runtime = SimpleNamespace(
        settings=RuntimeSettings(viewer_visual_input_mode=ViewerVisualInputMode.DIRECT_FRAMES)
    )
    coordinator = ViewerRuntimeCoordinator(runtime_state=object(), viewer_runtime=object())

    prepared = await coordinator._prepare_visual_wave(wave, runtime)

    assert prepared is not None
    assert prepared.frame_bundle is not None
    assert [frame.frame_id for frame in prepared.frame_bundle.frames] == ["trigger", "fresh"]
    assert [frame.frame_index for frame in prepared.frame_bundle.frames] == [0, 1]
