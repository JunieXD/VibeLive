from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from advx_backend.application.ports.session import Clock
from advx_backend.domain.crowd_decision import CrowdDecision, DecisionSource
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.observation_wave import ObservationWave


class DirectorProvider(Protocol):
    async def decide(self, request: object) -> object: ...


class DirectorBudgetPolicy(Protocol):
    def maximum(self, **context: object) -> int: ...


class DirectorFallbackPolicy(Protocol):
    def decide(self, **context: object) -> CrowdDecision: ...


class DirectorDecisionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DirectorRequest:
    wave: ObservationWave
    viewer_ids: tuple[str, ...]
    maximum: int
    runtime: object
    forced_viewer_ids: tuple[str, ...] = ()
    forced_persona_id: str | None = None


@dataclass(frozen=True, slots=True)
class DirectorOutcome:
    decision: CrowdDecision
    meme_candidate: MemeCandidate | None = None


def validate_meme_candidate(
    candidate: MemeCandidate | None,
    *,
    wave: ObservationWave,
    runtime: object,
) -> None:
    if candidate is None:
        return
    if not isinstance(candidate, MemeCandidate):
        raise DirectorDecisionError("Director returned an invalid meme candidate")
    if (
        candidate.room_id != wave.room_id
        or candidate.session_id != wave.session_id
        or candidate.audience_epoch != wave.audience_epoch
        or candidate.observation_id != wave.observation_id
    ):
        raise DirectorDecisionError("Meme candidate scope does not match the wave")
    runtime_spec = getattr(runtime, "canonical_runtime_spec", runtime)
    active_mode_id = getattr(runtime_spec, "active_mode_id", None)
    active_mode = next(
        (
            mode
            for mode in getattr(runtime_spec, "modes", ())
            if getattr(mode, "mode_id", None) == active_mode_id
        ),
        None,
    )
    if candidate.namespace_id != getattr(active_mode, "namespace_id", None):
        raise DirectorDecisionError("Meme candidate namespace is not the active mode")
    if not set(candidate.evidence_event_ids).issubset(wave.event_ids):
        raise DirectorDecisionError("Meme candidate referenced an unknown event")
    frame_count = 0 if wave.frame_bundle is None else len(wave.frame_bundle.frames)
    if any(index < 0 or index >= frame_count for index in candidate.evidence_frame_indexes):
        raise DirectorDecisionError("Meme candidate referenced an unknown frame")


class DirectorService:
    """Invoke the Director exactly once and validate its bounded identity decision."""

    def __init__(
        self,
        *,
        provider: DirectorProvider,
        budget_policy: DirectorBudgetPolicy,
        fallback: DirectorFallbackPolicy,
        clock: Clock,
    ) -> None:
        self._provider = provider
        self._budget_policy = budget_policy
        self._fallback = fallback
        self._clock = clock

    async def decide(
        self,
        *,
        wave: ObservationWave,
        pool: object,
        runtime: object,
    ) -> DirectorOutcome:
        viewers = tuple(getattr(pool, "viewers", ()))
        viewer_ids = tuple(viewer.viewer_instance_id for viewer in viewers)
        maximum = max(
            0,
            min(
                len(viewer_ids),
                int(
                    self._budget_policy.maximum(
                        wave=wave,
                        pool=pool,
                        runtime=runtime,
                    )
                ),
            ),
        )
        if (wave.target_viewer_id or wave.target_persona_id) and viewer_ids:
            maximum = max(1, maximum)
        request = DirectorRequest(
            wave=wave,
            viewer_ids=viewer_ids,
            maximum=maximum,
            runtime=runtime,
            forced_viewer_ids=(
                (wave.target_viewer_id,)
                if wave.target_viewer_id is not None
                else ()
            ),
            forced_persona_id=wave.target_persona_id,
        )
        try:
            raw = await self._provider.decide(request)
            outcome = self._coerce_outcome(raw)
            self._validate(
                outcome.decision,
                wave=wave,
                viewers=viewers,
                viewer_ids=set(viewer_ids),
                maximum=maximum,
            )
        except Exception as error:
            if self._is_resilient(runtime):
                fallback = self._fallback.decide(
                    wave=wave,
                    pool=pool,
                    runtime=runtime,
                    maximum=maximum,
                )
                outcome = DirectorOutcome(
                    decision=fallback.model_copy(
                        update={"decision_source": DecisionSource.FALLBACK}
                    )
                )
                self._validate(
                    outcome.decision,
                    wave=wave,
                    viewers=viewers,
                    viewer_ids=set(viewer_ids),
                    maximum=maximum,
                )
            else:
                raise DirectorDecisionError(str(error)) from error
        validate_meme_candidate(
            outcome.meme_candidate,
            wave=wave,
            runtime=runtime,
        )
        return outcome

    @staticmethod
    def _coerce_outcome(raw: object) -> DirectorOutcome:
        if isinstance(raw, DirectorOutcome):
            return raw
        if isinstance(raw, CrowdDecision):
            return DirectorOutcome(decision=raw)
        decision = getattr(raw, "decision", None)
        if isinstance(decision, CrowdDecision):
            candidate = getattr(raw, "meme_candidate", None)
            return DirectorOutcome(decision=decision, meme_candidate=candidate)
        raise DirectorDecisionError("Director returned an invalid result")

    def _validate(
        self,
        decision: CrowdDecision,
        *,
        wave: ObservationWave,
        viewers: tuple[object, ...],
        viewer_ids: set[str],
        maximum: int,
    ) -> None:
        if (
            decision.room_id != wave.room_id
            or decision.session_id != wave.session_id
            or decision.audience_epoch != wave.audience_epoch
            or decision.observation_id != wave.observation_id
        ):
            raise DirectorDecisionError("Director decision scope does not match the wave")
        if len(decision.selected_viewer_ids) > maximum:
            raise DirectorDecisionError("Director exceeded the local response budget")
        if not set(decision.selected_viewer_ids).issubset(viewer_ids):
            raise DirectorDecisionError("Director selected an unknown Viewer")
        if (
            wave.target_viewer_id is not None
            and wave.target_viewer_id not in decision.selected_viewer_ids
        ):
            raise DirectorDecisionError("Director omitted the explicitly targeted Viewer")
        if wave.target_persona_id is not None:
            selected = set(decision.selected_viewer_ids)
            if not any(
                viewer.viewer_instance_id in selected
                and viewer.persona_id == wave.target_persona_id
                for viewer in viewers
            ):
                raise DirectorDecisionError("Director omitted the explicitly targeted Persona")
        if not set(decision.evidence_event_ids).issubset(wave.event_ids):
            raise DirectorDecisionError("Director referenced an unknown event")
        frame_count = 0 if wave.frame_bundle is None else len(wave.frame_bundle.frames)
        if any(index >= frame_count for index in decision.evidence_frame_indexes):
            raise DirectorDecisionError("Director referenced an unknown frame")
        if self._clock.now_ms() >= decision.expires_at_ms:
            raise DirectorDecisionError("Director decision is already expired")

    @staticmethod
    def _is_resilient(runtime: object) -> bool:
        settings = getattr(runtime, "settings", None)
        mode = getattr(settings, "director_failure_mode", None)
        return getattr(mode, "value", mode) == "resilient"
