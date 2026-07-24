import pytest
from pydantic import ValidationError

from advx_backend.domain.observation_wave import (
    FrameBundle,
    FrameBundleItem,
    FrameBundleSettings,
    FrameSelectionStrategy,
    ObservationTrigger,
    ObservationWave,
)


def frame(index: int, *, captured_at_ms: int, change_score: float) -> FrameBundleItem:
    return FrameBundleItem(
        frame_id=f"frame-{index}",
        frame_index=index,
        captured_at_ms=captured_at_ms,
        width=1280,
        height=720,
        encoding="image/webp",
        content_hash=f"{index + 1:064x}",
        data_ref=f"memory://frame-{index}",
        change_score=change_score,
    )


def test_latest_n_frame_strategy_selects_the_most_recent_frames_without_duplicates() -> None:
    from advx_backend.application.observation_wave_builder import select_frame_bundle

    selected = select_frame_bundle(
        frames=[
            frame(0, captured_at_ms=100, change_score=0.1),
            frame(1, captured_at_ms=200, change_score=0.3),
            frame(2, captured_at_ms=300, change_score=0.2),
            frame(3, captured_at_ms=400, change_score=0.4),
        ],
        settings=FrameBundleSettings(
            frame_bundle_size=3,
            frame_window_ms=1_000,
            frame_selection_strategy=FrameSelectionStrategy.LATEST_N,
        ),
        now_ms=500,
    )

    assert [item.frame_id for item in selected] == ["frame-1", "frame-2", "frame-3"]


def test_evenly_spaced_strategy_covers_the_history_window_endpoints() -> None:
    from advx_backend.application.observation_wave_builder import select_frame_bundle

    selected = select_frame_bundle(
        frames=[
            frame(index, captured_at_ms=index * 100, change_score=0.1)
            for index in range(7)
        ],
        settings=FrameBundleSettings(
            frame_bundle_size=3,
            frame_window_ms=1_000,
            frame_selection_strategy=FrameSelectionStrategy.EVENLY_SPACED,
        ),
        now_ms=700,
    )

    assert [item.frame_id for item in selected] == ["frame-0", "frame-3", "frame-6"]


def test_change_peaks_strategy_selects_highest_changes_then_restores_time_order() -> None:
    from advx_backend.application.observation_wave_builder import select_frame_bundle

    selected = select_frame_bundle(
        frames=[
            frame(0, captured_at_ms=100, change_score=0.2),
            frame(1, captured_at_ms=200, change_score=0.9),
            frame(2, captured_at_ms=300, change_score=0.4),
            frame(3, captured_at_ms=400, change_score=0.8),
        ],
        settings=FrameBundleSettings(
            frame_bundle_size=2,
            frame_window_ms=1_000,
            frame_selection_strategy=FrameSelectionStrategy.CHANGE_PEAKS,
        ),
        now_ms=500,
    )

    assert [item.frame_id for item in selected] == ["frame-1", "frame-3"]


def test_frame_selection_returns_available_history_instead_of_copying_frames() -> None:
    from advx_backend.application.observation_wave_builder import select_frame_bundle

    only = frame(0, captured_at_ms=100, change_score=0.5)
    selected = select_frame_bundle(
        frames=[only],
        settings=FrameBundleSettings(frame_bundle_size=3),
        now_ms=100,
    )

    assert selected == (only,)


def test_partial_voice_is_not_a_formal_observation_trigger() -> None:
    with pytest.raises(ValueError):
        ObservationTrigger("partial_voice")


def test_final_voice_is_a_formal_observation_trigger() -> None:
    assert ObservationTrigger("final_voice") is ObservationTrigger.FINAL_VOICE


def test_observation_wave_is_frozen_from_later_frame_mutation() -> None:
    original_frames = [frame(0, captured_at_ms=100, change_score=0.5)]
    bundle = FrameBundle(
        bundle_id="bundle-1",
        settings=FrameBundleSettings(frame_bundle_size=2),
        frames=original_frames,
    )
    wave = ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="wave-1",
        created_at_ms=100,
        deadline_at_ms=1_000,
        triggers=[ObservationTrigger.FINAL_VOICE],
        event_ids=["voice-final-1"],
        frame_bundle=bundle,
    )

    original_frames.append(frame(1, captured_at_ms=200, change_score=0.6))

    assert [item.frame_id for item in wave.frame_bundle.frames] == ["frame-0"]
    with pytest.raises(ValidationError):
        wave.audience_epoch = 2
