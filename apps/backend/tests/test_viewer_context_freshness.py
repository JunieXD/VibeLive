from types import SimpleNamespace

import pytest

from advx_backend.application.viewer_runtime_coordinator import (
    FrameMetadata,
    ViewerRuntimeCoordinator,
)
from advx_backend.application.visual_signature import VISUAL_SIGNATURE_BYTES
from advx_backend.bootstrap import BackendRuntime
from advx_backend.contracts.viewer_runtime import BarrageGenerationMode, RuntimeSettings
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.observation import FrameRef, Observation
from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    FrameBundleSettings,
    FrameSelectionStrategy,
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


def test_window_batch_context_uses_recent_text_and_both_final_asr_sources() -> None:
    events = (
        _event(1, RoomEventSource.USER_TEXT, created_at_ms=69_999),
        _event(2, RoomEventSource.SCREEN_OBSERVATION, created_at_ms=90_000),
        _event(3, RoomEventSource.AUDIENCE_BARRAGE, created_at_ms=91_000),
        _event(4, RoomEventSource.USER_VOICE, created_at_ms=92_000),
        _event(
            5,
            RoomEventSource.SYSTEM_EVENT,
            created_at_ms=93_000,
            payload={"event": "system_audio_transcript"},
        ),
        _event(6, RoomEventSource.USER_TEXT, created_at_ms=99_000),
    )
    observation = Observation(
        session_id="session",
        observation_id="window",
        created_at_ms=100_000,
        room_events=events,
    )

    public_context, reply_context = (
        ViewerRuntimeCoordinator._select_window_batch_contexts(
            observation,
            RuntimeSettings(
                barrage_generation_mode=BarrageGenerationMode.WINDOW_BATCH
            ),
        )
    )

    assert [event.event_id for event in public_context] == [
        "event-4",
        "event-5",
        "event-6",
    ]
    assert reply_context == ()


@pytest.mark.parametrize(
    "override",
    [
        {"window_batch_interval_ms": 4_000},
        {"window_batch_context_window_ms": 20_000},
        {"window_batch_max_frames": 4},
    ],
)
def test_window_batch_runtime_settings_enforce_the_fixed_preset(
    override: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="window_batch requires"):
        RuntimeSettings(
            barrage_generation_mode=BarrageGenerationMode.WINDOW_BATCH,
            **override,
        )


@pytest.mark.asyncio
async def test_window_batch_frame_bundle_is_30_seconds_max_five_and_keeps_trigger() -> None:
    class Metadata:
        async def resolve(self, *, session_id: str, frame: FrameRef) -> FrameMetadata:
            del session_id
            return FrameMetadata(
                width=1280,
                height=720,
                encoding="jpeg",
                content_hash=f"{int(frame.frame_id):064x}",
                change_score=int(frame.frame_id) / 10,
            )

    frames = tuple(
        FrameRef(
            frame_id=str(index),
            created_at_ms=50_000 + index * 7_000,
            mime_type="image/jpeg",
            data_ref=f"frame:{index}",
        )
        for index in range(1, 8)
    )
    observation = Observation(
        session_id="session",
        observation_id="window",
        created_at_ms=100_000,
        frames=frames,
        trigger_frame_ids=("3",),
        user_context={"ambient": "true"},
    )
    committed = SimpleNamespace(
        audience_epoch=1,
        spec=SimpleNamespace(
            room=SimpleNamespace(room_id="room"),
            settings=RuntimeSettings(
                barrage_generation_mode=BarrageGenerationMode.WINDOW_BATCH
            ),
        ),
    )
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=object(),
        viewer_runtime=object(),
        frame_metadata=Metadata(),
    )

    wave = await coordinator._build_wave(observation, committed)

    assert wave.frame_bundle is not None
    assert len(wave.frame_bundle.frames) == 5
    assert wave.frame_bundle.settings.frame_window_ms == 30_000
    assert all(frame.captured_at_ms >= 70_000 for frame in wave.frame_bundle.frames)
    assert "3" in [frame.frame_id for frame in wave.frame_bundle.frames]


@pytest.mark.asyncio
async def test_frame_bundle_keeps_trigger_and_newest_ordinary_frames() -> None:
    class Metadata:
        async def resolve(self, *, session_id: str, frame: FrameRef) -> FrameMetadata:
            del session_id
            return FrameMetadata(
                width=1280,
                height=720,
                encoding="jpeg",
                content_hash=f"{int(frame.frame_id) + 1:064x}",
                change_score=0.0,
            )

    observation = Observation(
        session_id="session",
        observation_id="newest-frames",
        created_at_ms=100_000,
        frames=tuple(
            FrameRef(
                frame_id=str(index),
                created_at_ms=94_000 + index * 1_000,
                mime_type="image/jpeg",
                data_ref=f"frame:{index}",
            )
            for index in range(7)
        ),
        trigger_frame_ids=("0",),
    )
    committed = SimpleNamespace(
        audience_epoch=1,
        spec=SimpleNamespace(
            room=SimpleNamespace(room_id="room"),
            settings=RuntimeSettings(
                frame_bundle=FrameBundleSettings(
                    frame_bundle_size=15,
                    frame_window_ms=120_000,
                    frame_selection_strategy=FrameSelectionStrategy.EVENLY_SPACED,
                    frame_similarity_threshold=0.9,
                )
            ),
        ),
    )
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=object(),
        viewer_runtime=object(),
        frame_metadata=Metadata(),
    )

    wave = await coordinator._build_wave(observation, committed)

    assert wave.frame_bundle is not None
    assert wave.frame_bundle.settings.frame_bundle_size == 5
    assert wave.frame_bundle.settings.frame_window_ms == 30_000
    assert wave.frame_bundle.settings.frame_similarity_threshold == 0.95
    assert wave.frame_bundle.settings.frame_selection_strategy is FrameSelectionStrategy.LATEST_N
    assert [frame.frame_id for frame in wave.frame_bundle.frames] == ["0", "3", "4", "5", "6"]


@pytest.mark.asyncio
async def test_frame_bundle_uses_visual_signatures_to_bound_gradual_scene_drift() -> None:
    class Metadata:
        async def resolve(self, *, session_id: str, frame: FrameRef) -> FrameMetadata:
            del session_id
            level = 0 if int(frame.frame_id) < 2 else 1
            return FrameMetadata(
                width=1280,
                height=720,
                encoding="jpeg",
                content_hash=f"{level + 1:064x}",
                change_score=0 if level == 0 else 1 / 15,
                visual_signature=bytes([(level << 4) | level]) * VISUAL_SIGNATURE_BYTES,
            )

    observation = Observation(
        session_id="session",
        observation_id="anchor-comparison",
        created_at_ms=2_000,
        frames=tuple(
            FrameRef(
                frame_id=str(index),
                created_at_ms=index * 1_000,
                mime_type="image/jpeg",
                data_ref=f"frame:{index}",
            )
            for index in range(3)
        ),
        trigger_frame_ids=("2",),
    )
    committed = SimpleNamespace(
        audience_epoch=1,
        spec=SimpleNamespace(
            room=SimpleNamespace(room_id="room"),
            settings=RuntimeSettings(
                frame_bundle=FrameBundleSettings(
                    frame_bundle_size=5,
                    frame_similarity_threshold=0.9,
                )
            ),
        ),
    )
    coordinator = ViewerRuntimeCoordinator(
        runtime_state=object(),
        viewer_runtime=object(),
        frame_metadata=Metadata(),
    )

    wave = await coordinator._build_wave(observation, committed)

    assert wave.frame_bundle is not None
    assert wave.frame_bundle.settings.frame_similarity_threshold == 0.95
    assert [frame.frame_id for frame in wave.frame_bundle.frames] == ["1", "2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "generation_mode",
        "ambience",
        "ambient_enabled",
        "ambient_interval_ms",
        "window_enabled",
        "window_interval_ms",
    ),
    [
        (
            BarrageGenerationMode.WINDOW_BATCH,
            "natural",
            False,
            30_000,
            True,
            5_000,
        ),
        (
            BarrageGenerationMode.PER_VIEWER,
            "continuous",
            True,
            30_000,
            False,
            5_000,
        ),
        (
            BarrageGenerationMode.PER_VIEWER,
            "natural",
            False,
            30_000,
            False,
            5_000,
        ),
    ],
)
async def test_ambient_schedule_depends_on_generation_mode(
    generation_mode: BarrageGenerationMode,
    ambience: str,
    ambient_enabled: bool,
    ambient_interval_ms: int,
    window_enabled: bool,
    window_interval_ms: int,
) -> None:
    class State:
        async def snapshot(self, session_id: str) -> object:
            del session_id
            return SimpleNamespace(
                spec=SimpleNamespace(
                    active_mode_id="mode",
                    modes=(
                        SimpleNamespace(
                            mode_id="mode",
                            ambience=SimpleNamespace(value=ambience),
                        ),
                    ),
                    settings=RuntimeSettings(
                        barrage_generation_mode=generation_mode
                    ),
                )
            )

    container = SimpleNamespace(runtime_state=State())

    assert (
        await BackendRuntime.ambient_enabled(container, "session")
        is ambient_enabled
    )
    assert (
        await BackendRuntime.ambient_interval_ms(container, "session")
        == ambient_interval_ms
    )
    assert await BackendRuntime.window_batch_schedule(container, "session") == (
        window_enabled,
        window_interval_ms,
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


def test_screen_waves_select_a_deterministic_quarter_and_keep_other_budgets() -> None:
    viewers = [
        _viewer(f"viewer-{index:02}", last_spoke_at_ms=None if index < 2 else index * 1_000)
        for index in range(10)
    ]
    viewers.append(_viewer("persona-old", persona_id="target", last_spoke_at_ms=20_000))
    viewers.append(_viewer("persona-fresh", persona_id="target", last_spoke_at_ms=None))
    committed = SimpleNamespace(
        pool=SimpleNamespace(viewers=viewers, session_seed="screen-selection-seed"),
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
    repeated_screen = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.SCREEN_CHANGE),
        committed=committed,
    )
    alternative_screen = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.SCREEN_CHANGE),
        committed=SimpleNamespace(
            pool=SimpleNamespace(viewers=viewers, session_seed="other-screen-selection-seed"),
            spec=SimpleNamespace(settings=RuntimeSettings()),
        ),
    )
    single_viewer_screen = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.SCREEN_CHANGE),
        committed=SimpleNamespace(
            pool=SimpleNamespace(viewers=[viewers[0]], session_seed="screen-selection-seed"),
            spec=SimpleNamespace(settings=RuntimeSettings()),
        ),
    )
    system_audio = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.SYSTEM_AUDIO),
        committed=committed,
    )
    ambient = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.AMBIENT_TICK),
        committed=committed,
    )
    mixed_window = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.USER_TEXT).model_copy(
            update={
                "triggers": [
                    ObservationTrigger.USER_TEXT,
                    ObservationTrigger.SCREEN_CHANGE,
                ]
            }
        ),
        committed=committed,
    )
    dual_asr_window = coordinator._decide_speakers(
        wave=_wave(ObservationTrigger.FINAL_VOICE).model_copy(
            update={
                "triggers": [
                    ObservationTrigger.FINAL_VOICE,
                    ObservationTrigger.SYSTEM_AUDIO,
                ]
            }
        ),
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
    assert len(screen.selected_viewer_ids) == 3
    assert set(screen.selected_viewer_ids).issubset(
        {viewer.viewer_instance_id for viewer in viewers}
    )
    assert screen.selected_viewer_ids == repeated_screen.selected_viewer_ids
    assert screen.selected_viewer_ids != alternative_screen.selected_viewer_ids
    assert single_viewer_screen.selected_viewer_ids == ["viewer-00"]
    assert system_audio.selected_viewer_ids == ["persona-fresh", "viewer-00"]
    assert ambient.selected_viewer_ids == ["persona-fresh", "viewer-00"]
    assert len(mixed_window.selected_viewer_ids) == 6
    assert len(dual_asr_window.selected_viewer_ids) == 6
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
