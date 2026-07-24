import hashlib

from advx_backend.domain.crowd_decision import DecisionSource
from advx_backend.domain.observation_wave import ObservationTrigger, ObservationWave
from advx_backend.domain.persona import ModeDefinition
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstance, ViewerLifecycleState


class ActiveModeDirectorBudgetPolicy:
    """Derive a hard local ceiling from the frozen Mode and eligible pool."""

    def maximum(self, **context: object) -> int:
        wave = context.get("wave")
        pool = context.get("pool")
        runtime = context.get("runtime")
        if not isinstance(wave, ObservationWave):
            return 0
        mode = _active_mode(runtime)
        if mode is None:
            return 0
        response_range = (
            mode.highlight_response_range
            if _is_highlight(wave)
            else mode.normal_response_range
        )
        eligible = _eligible_viewers(pool, wave)
        return min(response_range.maximum, len(eligible))


class DeterministicDirectorFallbackPolicy:
    """Return a bounded local scene assessment without selecting speakers."""

    def decide(self, **context: object) -> SceneAssessment:
        wave = context.get("wave")
        if not isinstance(wave, ObservationWave):
            raise TypeError("fallback requires an ObservationWave")
        maximum = max(0, int(context.get("maximum", 0)))
        decision_digest = hashlib.sha256(
            (
                f"{wave.room_id}\0{wave.session_id}\0{wave.audience_epoch}\0"
                f"{wave.observation_id}\0scene-fallback-v2"
            ).encode()
        ).hexdigest()
        return SceneAssessment(
            assessment_id=f"fallback-{decision_digest[:32]}",
            room_id=wave.room_id,
            session_id=wave.session_id,
            audience_epoch=wave.audience_epoch,
            observation_id=wave.observation_id,
            salience=0.5,
            novelty=0.5,
            emotional_intensity=0.25,
            replyable_event_ids=list(wave.event_ids),
            evidence_event_ids=list(wave.trigger_event_ids),
            maximum_responses=min(maximum, 2),
            reason_codes=["director_failure_fallback"],
            decision_source=DecisionSource.FALLBACK,
            created_at_ms=wave.created_at_ms,
            expires_at_ms=wave.deadline_at_ms,
        )


def _active_mode(runtime: object) -> ModeDefinition | None:
    spec = getattr(runtime, "canonical_runtime_spec", runtime)
    active_mode_id = getattr(spec, "active_mode_id", None)
    return next(
        (
            mode
            for mode in getattr(spec, "modes", ())
            if isinstance(mode, ModeDefinition) and mode.mode_id == active_mode_id
        ),
        None,
    )


def _is_highlight(wave: ObservationWave) -> bool:
    return bool(
        {ObservationTrigger.USER_TEXT, ObservationTrigger.FINAL_VOICE}
        & set(wave.triggers)
    )


def _eligible_viewers(pool: object, wave: ObservationWave) -> list[ViewerInstance]:
    return [
        viewer
        for viewer in getattr(pool, "viewers", ())
        if isinstance(viewer, ViewerInstance)
        and viewer.lifecycle_state is ViewerLifecycleState.ACTIVE
        and viewer.room_id == wave.room_id
        and viewer.session_id == wave.session_id
        and viewer.audience_epoch == wave.audience_epoch
        and not viewer.is_muted(wave.created_at_ms)
        and (
            viewer.private_state.cooldown_until_ms is None
            or viewer.private_state.cooldown_until_ms <= wave.created_at_ms
        )
    ]
