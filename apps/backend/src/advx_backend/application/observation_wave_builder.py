from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from advx_backend.application.visual_signature import (
    is_visual_signature,
    visual_signature_change_score,
)
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
    visual_signatures: Mapping[str, bytes] | None = None,
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

    return _similar_frame_representatives(candidates, count, settings, visual_signatures)


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


def _similar_frame_representatives(
    candidates: tuple[FrameBundleItem, ...],
    count: int,
    settings: FrameBundleSettings,
    visual_signatures: Mapping[str, bytes] | None,
) -> tuple[FrameBundleItem, ...]:
    """Keep the newest frame from each bounded, anchor-relative visual segment."""

    groups: list[list[FrameBundleItem]] = [[candidates[0]]]
    for frame in candidates[1:]:
        anchor = groups[-1][0]
        elapsed_ms = frame.captured_at_ms - anchor.captured_at_ms
        if (
            elapsed_ms <= settings.frame_anchor_interval_ms
            and _is_similar_to_group_anchor(
                anchor,
                frame,
                threshold=settings.frame_similarity_threshold,
                visual_signatures=visual_signatures,
            )
        ):
            groups[-1].append(frame)
        else:
            groups.append([frame])

    selected = tuple(group[-1] for group in groups)
    return _evenly_spaced(selected, count)


def _is_similar_to_group_anchor(
    anchor: FrameBundleItem,
    frame: FrameBundleItem,
    *,
    threshold: float,
    visual_signatures: Mapping[str, bytes] | None,
) -> bool:
    if anchor.content_hash == frame.content_hash:
        return True
    if visual_signatures is None:
        return False
    anchor_signature = visual_signatures.get(anchor.frame_id)
    frame_signature = visual_signatures.get(frame.frame_id)
    if not is_visual_signature(anchor_signature) or not is_visual_signature(frame_signature):
        return False
    similarity = 1 - visual_signature_change_score(anchor_signature, frame_signature)
    return similarity >= threshold


@dataclass(frozen=True, slots=True)
class ObservationWaveBuildInput:
    frames: tuple[FrameBundleItem, ...]
    settings: FrameBundleSettings
    now_ms: int
    visual_signatures: Mapping[str, bytes] | None = None

    def select_frames(self) -> tuple[FrameBundleItem, ...]:
        return select_frame_bundle(
            frames=self.frames,
            settings=self.settings,
            now_ms=self.now_ms,
            visual_signatures=self.visual_signatures,
        )
