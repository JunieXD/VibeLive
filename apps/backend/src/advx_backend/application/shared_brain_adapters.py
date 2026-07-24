from collections.abc import Awaitable, Callable
from dataclasses import replace

from advx_backend.application.memory_extractor import RoomMemoryExtractor
from advx_backend.application.ports.memory import MemoryEvidence
from advx_backend.application.shared_brain_service import SharedBrainService
from advx_backend.application.viewer_runtime import ViewerDispatchSummary
from advx_backend.application.viewer_runtime_coordinator import FrozenWaveRuntime
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.observation_wave import ObservationWave


class SharedBrainMemeCandidateSink:
    def __init__(self, service: SharedBrainService) -> None:
        self._service = service

    async def commit_candidate(self, candidate: MemeCandidate) -> object:
        return await self._service.commit_meme_candidate(candidate)


class SharedBrainMemoryExtractionSink:
    """Extract public evidence after a wave without delaying Viewer delivery."""

    def __init__(
        self,
        *,
        extractor: RoomMemoryExtractor,
        service: SharedBrainService,
    ) -> None:
        self._extractor = extractor
        self._service = service

    async def extract_after_wave(
        self,
        *,
        wave: ObservationWave,
        decision: CrowdDecision,
        dispatch: ViewerDispatchSummary,
        runtime: FrozenWaveRuntime,
    ) -> None:
        async def commit_directly(
            operation: Callable[[], Awaitable[object]],
        ) -> tuple[bool, object | None]:
            return True, await operation()

        await self.extract_after_wave_fenced(
            wave=wave,
            decision=decision,
            dispatch=dispatch,
            runtime=runtime,
            commit_effect=commit_directly,
        )

    async def extract_after_wave_fenced(
        self,
        *,
        wave: ObservationWave,
        decision: CrowdDecision,
        dispatch: ViewerDispatchSummary,
        runtime: FrozenWaveRuntime,
        commit_effect: Callable[
            [Callable[[], Awaitable[object]]],
            Awaitable[tuple[bool, object | None]],
        ],
    ) -> None:
        del decision, dispatch
        evidence = tuple(
            MemoryEvidence(
                event_id=event.event_id,
                room_id=wave.room_id,
                source_type=event.source_type.value,
                occurred_at_ms=event.created_at_ms,
                summary=event.text or event.source_type.value,
            )
            for event in runtime.public_context
        )
        revision = runtime.room_memory_slice.memory_revision
        candidates = await self._extractor.extract(
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            events=evidence,
            current_revision=revision,
        )
        for candidate in candidates:
            accepted, result = await commit_effect(
                lambda candidate=candidate, revision=revision: (
                    self._service.commit_memory_candidate(
                        replace(candidate, base_revision=revision)
                    )
                )
            )
            if not accepted or result is None:
                break
            if result.accepted and result.memory_revision is not None:
                revision = result.memory_revision


__all__ = [
    "SharedBrainMemoryExtractionSink",
    "SharedBrainMemeCandidateSink",
]
