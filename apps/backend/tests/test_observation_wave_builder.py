from advx_backend.application.observation_wave_builder import select_frame_bundle
from advx_backend.application.visual_signature import VISUAL_SIGNATURE_BYTES
from advx_backend.domain.observation_wave import (
    DEFAULT_FRAME_SIMILARITY_THRESHOLD,
    FRAME_BUNDLE_SELECTION_LIMIT,
    MAX_FRAME_BUNDLE_SIZE,
    MAX_FRAME_WINDOW_MS,
    FrameBundleItem,
    FrameBundleSettings,
    FrameSelectionStrategy,
)


def _frame(index: int, *, captured_at_ms: int, change_score: float = 0.0) -> FrameBundleItem:
    return FrameBundleItem(
        frame_id=f"frame-{index}",
        frame_index=index,
        captured_at_ms=captured_at_ms,
        width=1280,
        height=720,
        encoding="image/jpeg",
        content_hash=f"{index + 1:064x}",
        data_ref=f"frame:{index}",
        change_score=change_score,
    )


def _signature(level: int) -> bytes:
    assert 0 <= level <= 15
    return bytes([(level << 4) | level]) * VISUAL_SIGNATURE_BYTES


def _partial_signature(changed_pixels: int) -> bytes:
    assert 0 <= changed_pixels <= VISUAL_SIGNATURE_BYTES * 2
    pixels = [1 if index < changed_pixels else 0 for index in range(VISUAL_SIGNATURE_BYTES * 2)]
    return bytes(
        (pixels[index] << 4) | pixels[index + 1]
        for index in range(0, len(pixels), 2)
    )


def test_change_peaks_uses_each_group_first_frame_as_its_visual_anchor() -> None:
    frames = (
        _frame(0, captured_at_ms=0),
        _frame(1, captured_at_ms=1_000, change_score=1 / 15),
        _frame(2, captured_at_ms=2_000, change_score=1 / 15),
    )

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=5, frame_similarity_threshold=0.9),
        now_ms=2_000,
        visual_signatures={
            "frame-0": _signature(0),
            "frame-1": _partial_signature(100),
            "frame-2": _partial_signature(250),
        },
    )

    # The second frame is close to the first, but the third differs enough from the
    # first segment anchor to start a new group.
    assert [frame.frame_id for frame in selected] == ["frame-1", "frame-2"]


def test_change_peaks_keeps_a_time_anchor_for_a_static_scene() -> None:
    frames = (
        _frame(0, captured_at_ms=0),
        _frame(1, captured_at_ms=5_000),
        _frame(2, captured_at_ms=10_000),
    )
    signatures = {frame.frame_id: _signature(0) for frame in frames}

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=5, frame_anchor_interval_ms=5_000),
        now_ms=10_000,
        visual_signatures=signatures,
    )

    assert [frame.frame_id for frame in selected] == ["frame-1", "frame-2"]


def test_change_peaks_keeps_legacy_frames_when_an_anchor_comparison_is_unavailable() -> None:
    frames = (
        _frame(0, captured_at_ms=0),
        _frame(1, captured_at_ms=1_000),
        _frame(2, captured_at_ms=2_000),
    )

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=5),
        now_ms=2_000,
    )

    assert [frame.frame_id for frame in selected] == ["frame-0", "frame-1", "frame-2"]


def test_frame_bundle_defaults_use_the_current_visual_limits() -> None:
    settings = FrameBundleSettings()

    assert MAX_FRAME_BUNDLE_SIZE == 15
    assert settings.frame_bundle_size == FRAME_BUNDLE_SELECTION_LIMIT == 5
    assert settings.frame_window_ms == MAX_FRAME_WINDOW_MS == 30_000
    assert settings.frame_similarity_threshold == DEFAULT_FRAME_SIMILARITY_THRESHOLD == 0.95
    legacy = FrameBundleSettings(
        frame_bundle_size=15,
        frame_window_ms=120_000,
        frame_similarity_threshold=0.9,
    )
    assert legacy.frame_bundle_size == 15
    assert legacy.frame_window_ms == 120_000
    assert legacy.frame_similarity_threshold == 0.9


def test_change_peaks_default_threshold_treats_small_visual_changes_as_distinct() -> None:
    frames = (
        _frame(0, captured_at_ms=0),
        _frame(1, captured_at_ms=1_000),
    )

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=5),
        now_ms=1_000,
        visual_signatures={
            "frame-0": _signature(0),
            "frame-1": _signature(1),
        },
    )

    assert [frame.frame_id for frame in selected] == ["frame-0", "frame-1"]


def test_legacy_settings_still_select_at_current_limits() -> None:
    frames = (
        _frame(0, captured_at_ms=69_000),
        _frame(1, captured_at_ms=70_000),
        _frame(2, captured_at_ms=80_000),
        _frame(3, captured_at_ms=85_000),
        _frame(4, captured_at_ms=90_000),
        _frame(5, captured_at_ms=95_000),
        _frame(6, captured_at_ms=100_000),
    )

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=15, frame_window_ms=120_000),
        now_ms=100_000,
    )

    assert [frame.frame_id for frame in selected] == [
        "frame-2",
        "frame-3",
        "frame-4",
        "frame-5",
        "frame-6",
    ]


def test_change_peaks_keeps_newest_representatives_when_over_capacity() -> None:
    frames = tuple(_frame(index, captured_at_ms=index * 1_000) for index in range(7))

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=5),
        now_ms=6_000,
        visual_signatures={
            frame.frame_id: _signature(index) for index, frame in enumerate(frames)
        },
    )

    assert [frame.frame_id for frame in selected] == [
        "frame-2",
        "frame-3",
        "frame-4",
        "frame-5",
        "frame-6",
    ]


def test_legacy_even_sampling_strategy_keeps_newest_frames_when_over_capacity() -> None:
    frames = tuple(_frame(index, captured_at_ms=index * 1_000) for index in range(7))

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(
            frame_bundle_size=5,
            frame_selection_strategy=FrameSelectionStrategy.EVENLY_SPACED,
        ),
        now_ms=6_000,
    )

    assert [frame.frame_id for frame in selected] == [
        "frame-2",
        "frame-3",
        "frame-4",
        "frame-5",
        "frame-6",
    ]
