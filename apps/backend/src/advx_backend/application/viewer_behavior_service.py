from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from advx_backend.domain.observation_wave import ObservationTrigger, ObservationWave
from advx_backend.domain.persona import PersonaTemplate
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import ViewerInstance


@dataclass(frozen=True, slots=True)
class ViewerDesire:
    viewer_instance_id: str
    persona_id: str
    eligible: bool
    reason: str
    probability: float
    draw: float
    score: float

    @property
    def selected(self) -> bool:
        return self.eligible and self.draw < self.probability


class ViewerBehaviorService:
    """Compute explainable, replayable per-Viewer speaking desire."""

    def evaluate(
        self,
        *,
        viewer: ViewerInstance,
        persona: PersonaTemplate,
        wave: ObservationWave,
        assessment: SceneAssessment,
        recent_speaker_count: int,
        crowd_pressure: float,
        session_seed: str,
    ) -> ViewerDesire:
        reason = self._ineligible_reason(viewer, wave)
        if reason is not None:
            return ViewerDesire(
                viewer_instance_id=viewer.viewer_instance_id,
                persona_id=viewer.persona_id,
                eligible=False,
                reason=reason,
                probability=0.0,
                draw=1.0,
                score=float("-inf"),
            )

        state = viewer.private_state
        topic_relevance = self._topic_relevance(persona, assessment)
        direct_mention = 1.0 if wave.target_viewer_id == viewer.viewer_instance_id else 0.0
        persona_mention = 0.7 if wave.target_persona_id == viewer.persona_id else 0.0
        real_input = bool(
            {
                ObservationTrigger.USER_TEXT,
                ObservationTrigger.FINAL_VOICE,
                ObservationTrigger.SYSTEM_AUDIO,
            }
            & set(wave.triggers)
        )
        emotional_activation = (
            assessment.emotional_intensity
            * (0.5 + 0.25 * viewer.variant.encouragement + 0.25 * viewer.variant.skepticism)
        )
        reply_impulse = (
            viewer.variant.reply_affinity if assessment.replyable_event_ids else 0.0
        )
        score = (
            -1.7
            + 1.6 * viewer.variant.activity_baseline
            + 1.2 * topic_relevance
            + 3.5 * direct_mention
            + 2.0 * persona_mention
            + 0.8 * reply_impulse
            + 0.9 * emotional_activation
            + 0.7 * assessment.novelty
            + 0.8 * state.engagement
            + (0.4 if real_input else -0.4)
            - 1.4 * persona.silence_bias
            - 1.0 * viewer.variant.silence_tendency
            - 1.1 * state.fatigue
            - 0.9 * min(recent_speaker_count, 3)
            - 1.2 * max(0.0, min(crowd_pressure, 1.0))
        )
        probability = 1.0 / (1.0 + math.exp(-score))
        draw = self._draw(
            session_seed,
            wave.audience_epoch,
            wave.observation_id,
            viewer.viewer_instance_id,
            viewer.behavior_revision,
        )
        return ViewerDesire(
            viewer_instance_id=viewer.viewer_instance_id,
            persona_id=viewer.persona_id,
            eligible=True,
            reason="candidate" if draw < probability else "sampled_silence",
            probability=probability,
            draw=draw,
            score=score,
        )

    @staticmethod
    def choose(
        desires: list[ViewerDesire],
        *,
        maximum: int,
        forced_viewer_id: str | None = None,
        forced_persona_id: str | None = None,
    ) -> tuple[str, ...]:
        if maximum <= 0:
            return ()
        selected = [item for item in desires if item.selected]
        selected.sort(
            key=lambda item: (
                -(item.probability - item.draw),
                item.viewer_instance_id,
            )
        )
        ids = [item.viewer_instance_id for item in selected[:maximum]]
        if forced_viewer_id is not None and forced_viewer_id not in ids:
            forced = next(
                (
                    item
                    for item in desires
                    if item.viewer_instance_id == forced_viewer_id and item.eligible
                ),
                None,
            )
            if forced is not None:
                ids = [forced_viewer_id, *ids]
                ids = ids[:maximum]
        elif forced_persona_id is not None and not any(
            item.persona_id == forced_persona_id
            and item.viewer_instance_id in ids
            for item in desires
        ):
            forced = max(
                (
                    item
                    for item in desires
                    if item.persona_id == forced_persona_id and item.eligible
                ),
                key=lambda item: (
                    item.probability - item.draw,
                    item.viewer_instance_id,
                ),
                default=None,
            )
            if forced is not None:
                ids = [forced.viewer_instance_id, *ids]
                ids = ids[:maximum]
        return tuple(dict.fromkeys(ids))

    @staticmethod
    def _ineligible_reason(
        viewer: ViewerInstance,
        wave: ObservationWave,
    ) -> str | None:
        if not viewer.is_active():
            return "viewer_not_active"
        if viewer.is_muted(wave.created_at_ms):
            return "viewer_muted"
        if (
            viewer.room_id != wave.room_id
            or viewer.session_id != wave.session_id
            or viewer.audience_epoch != wave.audience_epoch
        ):
            return "viewer_scope_mismatch"
        cooldown = viewer.private_state.cooldown_until_ms
        if cooldown is not None and cooldown > wave.created_at_ms:
            return "viewer_cooldown"
        return None

    @staticmethod
    def _topic_relevance(
        persona: PersonaTemplate,
        assessment: SceneAssessment,
    ) -> float:
        if not assessment.topics:
            return 0.0
        preferences = {
            item.strip().casefold()
            for item in (*persona.trigger_preferences, *persona.traits)
            if item.strip()
        }
        if not preferences:
            return 0.25
        topics = {item.strip().casefold() for item in assessment.topics if item.strip()}
        overlap = len(preferences & topics)
        return min(1.0, 0.2 + overlap / max(1, len(topics)))

    @staticmethod
    def _draw(
        session_seed: str,
        audience_epoch: int,
        observation_id: str,
        viewer_instance_id: str,
        behavior_revision: int,
    ) -> float:
        digest = hashlib.sha256(
            (
                f"{session_seed}\0{audience_epoch}\0{observation_id}\0"
                f"{viewer_instance_id}\0{behavior_revision}\0speak-v1"
            ).encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
