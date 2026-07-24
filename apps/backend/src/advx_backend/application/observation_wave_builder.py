from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from advx_backend.domain.observation_wave import (
    FrameBundleItem,
    FrameBundleSettings,
    FrameSelectionStrategy,
)


def select_frame_bundle(
    *,
    frames: Sequence[FrameBundleItem],
    settings: FrameBundleSettings,
    now_ms: int,
) -> tuple[FrameBundleItem, ...]:
    """Select unique historical frames without manufacturing missing history."""

    window_start = now_ms - settings.frame_window_ms
    by_id: dict[str, FrameBundleItem] = {}
    for frame in sorted(frames, key=lambda item: (item.captured_at_ms, item.frame_id)):
        if frame.captured_at_ms < window_start or frame.captured_at_ms > now_ms:
            continue
        by_id[frame.frame_id] = frame
    candidates = tuple(
        sorted(by_id.values(), key=lambda item: (item.captured_at_ms, item.frame_id))
    )
    count = min(settings.frame_bundle_size, len(candidates))
    if count == 0:
        return ()

    if settings.frame_selection_strategy is FrameSelectionStrategy.LATEST_N:
        return candidates[-count:]
    if settings.frame_selection_strategy is FrameSelectionStrategy.EVENLY_SPACED:
        return _evenly_spaced(candidates, count)

    return _smart_timeline(candidates, count, settings)


def _evenly_spaced(
    candidates: tuple[FrameBundleItem, ...],
    count: int,
) -> tuple[FrameBundleItem, ...]:
    if count >= len(candidates):
        return candidates
    if count == 1:
        return (candidates[-1],)
    last = len(candidates) - 1
    indexes = [round(index * last / (count - 1)) for index in range(count)]
    return tuple(candidates[index] for index in indexes)


def _change_score(frame: FrameBundleItem) -> float:
    return frame.change_score


def _smart_timeline(
    candidates: tuple[FrameBundleItem, ...],
    count: int,
    settings: FrameBundleSettings,
) -> tuple[FrameBundleItem, ...]:
    """Keep a chronological visual story while removing near-duplicate frames."""

    if count >= len(candidates):
        return candidates

    change_threshold = 1 - settings.frame_similarity_threshold
    anchors: list[FrameBundleItem] = [candidates[0]]
    changes: list[FrameBundleItem] = []
    last_anchor_at_ms = candidates[0].captured_at_ms
    for frame in candidates[1:]:
        if frame.captured_at_ms - last_anchor_at_ms >= settings.frame_anchor_interval_ms:
            anchors.append(frame)
            last_anchor_at_ms = frame.captured_at_ms
        if _change_score(frame) > change_threshold:
            changes.append(frame)

    chosen: dict[str, FrameBundleItem] = {frame.frame_id: frame for frame in anchors}
    remaining = count - len(chosen)
    if remaining > 0:
        for frame in sorted(
            changes,
            key=lambda item: (-_change_score(item), -item.captured_at_ms, item.frame_id),
        ):
            if frame.frame_id in chosen:
                continue
            chosen[frame.frame_id] = frame
            if len(chosen) >= count:
                break

    if len(chosen) < count:
        for frame in _evenly_spaced(candidates, count):
            chosen.setdefault(frame.frame_id, frame)
            if len(chosen) >= count:
                break

    selected = sorted(chosen.values(), key=lambda item: (item.captured_at_ms, item.frame_id))
    if len(selected) > count:
        selected = list(_evenly_spaced(tuple(selected), count))
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class ObservationWaveBuildInput:
    frames: tuple[FrameBundleItem, ...]
    settings: FrameBundleSettings
    now_ms: int

    def select_frames(self) -> tuple[FrameBundleItem, ...]:
        return select_frame_bundle(
            frames=self.frames,
            settings=self.settings,
            now_ms=self.now_ms,
        )
