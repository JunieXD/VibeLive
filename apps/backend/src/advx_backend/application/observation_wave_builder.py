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

    selected = sorted(
        candidates,
        key=lambda item: (
            -_change_score(item),
            -item.captured_at_ms,
            item.frame_id,
        ),
    )[:count]
    return tuple(sorted(selected, key=lambda item: (item.captured_at_ms, item.frame_id)))


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
