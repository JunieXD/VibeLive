from advx_backend.application.observation_wave_builder import select_frame_bundle
from advx_backend.application.visual_signature import VISUAL_SIGNATURE_BYTES
from advx_backend.domain.observation_wave import FrameBundleItem, FrameBundleSettings


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


def test_change_peaks_uses_each_group_first_frame_as_its_visual_anchor() -> None:
    frames = (
        _frame(0, captured_at_ms=0),
        _frame(1, captured_at_ms=1_000, change_score=1 / 15),
        _frame(2, captured_at_ms=2_000, change_score=1 / 15),
    )

    selected = select_frame_bundle(
        frames=frames,
        settings=FrameBundleSettings(frame_bundle_size=15, frame_similarity_threshold=0.9),
        now_ms=2_000,
        visual_signatures={
            "frame-0": _signature(0),
            "frame-1": _signature(1),
            "frame-2": _signature(2),
        },
    )

    # Adjacent deltas are both below 10%, but the third frame has drifted more than 10%
    # from the first. The first segment therefore keeps its latest frame, frame-1.
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
        settings=FrameBundleSettings(frame_bundle_size=15, frame_anchor_interval_ms=5_000),
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
        settings=FrameBundleSettings(frame_bundle_size=15),
        now_ms=2_000,
    )

    assert [frame.frame_id for frame in selected] == ["frame-0", "frame-1", "frame-2"]
