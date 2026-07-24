from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from advx_backend.application.ports.session import Clock
from advx_backend.domain.crowd_decision import CrowdDecision, DecisionSource
from advx_backend.domain.meme import MemeCandidate
from advx_backend.domain.observation_wave import ObservationTrigger, ObservationWave
from advx_backend.domain.scene_assessment import SceneAssessment


class DirectorProvider(Protocol):
    async def decide(self, request: object) -> object: ...


class DirectorBudgetPolicy(Protocol):
    def maximum(self, **context: object) -> int: ...


class DirectorFallbackPolicy(Protocol):
    def decide(self, **context: object) -> object: ...


class DirectorDecisionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DirectorRequest:
    wave: ObservationWave
    maximum: int
    runtime: object
    forced_viewer_ids: tuple[str, ...] = ()
    forced_persona_id: str | None = None


@dataclass(frozen=True, slots=True)
class DirectorOutcome:
    assessment: SceneAssessment
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
    """Produce one scene assessment without centrally selecting speakers."""

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
        maximum = max(
            0,
            min(
                len(viewers),
                int(
                    self._budget_policy.maximum(
                        wave=wave,
                        pool=pool,
                        runtime=runtime,
                    )
                ),
            ),
        )
        if (wave.target_viewer_id or wave.target_persona_id) and viewers:
            maximum = max(1, maximum)
        request = DirectorRequest(
            wave=wave,
            maximum=maximum,
            runtime=runtime,
            forced_viewer_ids=(
                (wave.target_viewer_id,) if wave.target_viewer_id is not None else ()
            ),
            forced_persona_id=wave.target_persona_id,
        )
        try:
            raw = await self._provider.decide(request)
            outcome = self._coerce_outcome(raw, wave=wave, maximum=maximum)
            self._validate(outcome.assessment, wave=wave, maximum=maximum)
        except Exception as error:
            if not self._is_resilient(runtime):
                raise DirectorDecisionError(str(error)) from error
            fallback = self._fallback.decide(
                wave=wave,
                pool=pool,
                runtime=runtime,
                maximum=maximum,
            )
            outcome = self._coerce_outcome(fallback, wave=wave, maximum=maximum)
            outcome = DirectorOutcome(
                assessment=outcome.assessment.model_copy(
                    update={"decision_source": DecisionSource.FALLBACK}
                )
            )
            self._validate(outcome.assessment, wave=wave, maximum=maximum)
        validate_meme_candidate(outcome.meme_candidate, wave=wave, runtime=runtime)
        return outcome

    @classmethod
    def _coerce_outcome(
        cls,
        raw: object,
        *,
        wave: ObservationWave,
        maximum: int,
    ) -> DirectorOutcome:
        if isinstance(raw, DirectorOutcome):
            return raw
        if isinstance(raw, SceneAssessment):
            return DirectorOutcome(assessment=raw)
        if isinstance(raw, CrowdDecision):
            return DirectorOutcome(
                assessment=cls._legacy_assessment(raw, wave=wave, maximum=maximum)
            )
        assessment = getattr(raw, "assessment", None)
        if isinstance(assessment, SceneAssessment):
            return DirectorOutcome(
                assessment=assessment,
                meme_candidate=getattr(raw, "meme_candidate", None),
            )
        decision = getattr(raw, "decision", None)
        if isinstance(decision, CrowdDecision):
            return DirectorOutcome(
                assessment=cls._legacy_assessment(
                    decision,
                    wave=wave,
                    maximum=maximum,
                ),
                meme_candidate=getattr(raw, "meme_candidate", None),
            )
        raise DirectorDecisionError("Director returned an invalid result")

    @staticmethod
    def _legacy_assessment(
        decision: CrowdDecision,
        *,
        wave: ObservationWave,
        maximum: int,
    ) -> SceneAssessment:
        highlight = bool(
            {ObservationTrigger.USER_TEXT, ObservationTrigger.FINAL_VOICE}
            & set(wave.triggers)
        )
        return SceneAssessment(
            assessment_id=decision.decision_id,
            room_id=decision.room_id,
            session_id=decision.session_id,
            audience_epoch=decision.audience_epoch,
            observation_id=decision.observation_id,
            salience=0.8 if highlight else 0.55,
            novelty=0.75,
            emotional_intensity=0.5,
            topics=list(decision.reason_codes),
            replyable_event_ids=list(decision.evidence_event_ids),
            evidence_event_ids=list(decision.evidence_event_ids),
            evidence_frame_indexes=list(decision.evidence_frame_indexes),
            maximum_responses=maximum,
            reason_codes=["legacy_director_assessment", *decision.reason_codes],
            decision_source=decision.decision_source,
            created_at_ms=decision.created_at_ms,
            expires_at_ms=decision.expires_at_ms,
        )

    def _validate(
        self,
        assessment: SceneAssessment,
        *,
        wave: ObservationWave,
        maximum: int,
    ) -> None:
        if (
            assessment.room_id != wave.room_id
            or assessment.session_id != wave.session_id
            or assessment.audience_epoch != wave.audience_epoch
            or assessment.observation_id != wave.observation_id
        ):
            raise DirectorDecisionError("Director assessment scope does not match the wave")
        if assessment.maximum_responses > maximum:
            raise DirectorDecisionError("Director exceeded the local response budget")
        if not set(assessment.evidence_event_ids).issubset(wave.event_ids):
            raise DirectorDecisionError("Director referenced an unknown event")
        if not set(assessment.replyable_event_ids).issubset(wave.event_ids):
            raise DirectorDecisionError("Director exposed an unknown replyable event")
        frame_count = 0 if wave.frame_bundle is None else len(wave.frame_bundle.frames)
        if any(index >= frame_count for index in assessment.evidence_frame_indexes):
            raise DirectorDecisionError("Director referenced an unknown frame")
        if self._clock.now_ms() >= assessment.expires_at_ms:
            raise DirectorDecisionError("Director assessment is already expired")

    @staticmethod
    def _is_resilient(runtime: object) -> bool:
        settings = getattr(runtime, "settings", None)
        mode = getattr(settings, "director_failure_mode", None)
        return getattr(mode, "value", mode) == "resilient"
