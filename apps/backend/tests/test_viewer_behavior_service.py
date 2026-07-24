import pytest

from advx_backend.application.runtime_state import CommittedRuntime, RuntimeStateStore
from advx_backend.application.viewer_behavior_service import ViewerBehaviorService
from advx_backend.application.viewer_pool_service import ViewerPoolSnapshot
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec, ProviderRuntimeSpec, Room
from advx_backend.domain.observation_wave import ObservationTrigger, ObservationWave
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.scene_assessment import SceneAssessment
from advx_backend.domain.viewer import (
    ViewerInstance,
    ViewerInstanceVariant,
    ViewerLifecycleState,
)


def _persona() -> PersonaTemplate:
    return PersonaTemplate(
        persona_id="curious",
        document_version=1,
        revision=1,
        content_hash="1" * 64,
        display_name="Curious",
        role="curious viewer",
        traits=["gameplay"],
        trigger_preferences=["gameplay"],
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.1,
        cooldown_ms=1_000,
    )


def _viewer() -> ViewerInstance:
    return ViewerInstance(
        viewer_instance_id="viewer-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        persona_id="curious",
        persona_revision=1,
        persona_content_hash="1" * 64,
        ordinal=1,
        username="pixel-user",
        display_name="pixel-user",
        avatar_seed="avatar-1",
        color_seed="color-1",
        variant=ViewerInstanceVariant(
            activity_baseline=0.8,
            attention_span=0.6,
            social_initiative=0.7,
            reply_affinity=0.9,
            expression_length=0.5,
            skepticism=0.4,
            encouragement=0.7,
            meme_affinity=0.3,
            focus="gameplay",
            silence_tendency=0.2,
            stay_duration_tendency=0.8,
            rejoin_tendency=0.6,
        ),
        joined_at_ms=100,
        join_count=1,
        created_at_ms=100,
    )


def _wave(*, target_viewer_id: str | None = None) -> ObservationWave:
    return ObservationWave(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        created_at_ms=1_000,
        deadline_at_ms=5_000,
        triggers=[ObservationTrigger.USER_TEXT],
        target_viewer_id=target_viewer_id,
    )


def _assessment() -> SceneAssessment:
    return SceneAssessment(
        assessment_id="assessment-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        salience=0.8,
        novelty=0.7,
        emotional_intensity=0.5,
        topics=["gameplay"],
        emotional_tone=["excited"],
        replyable_event_ids=["event-1"],
        evidence_event_ids=["event-1"],
        maximum_responses=2,
        created_at_ms=1_000,
        expires_at_ms=5_000,
    )


def _spec() -> CanonicalRuntimeSpec:
    persona = _persona()
    mode = ModeDefinition(
        mode_id="default",
        namespace_id="default",
        revision=1,
        target_concurrent_viewers=1,
        persona_ids=[persona.persona_id],
        persona_weights={persona.persona_id: 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=0, maximum=1),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=100,
            updated_at_ms=100,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


def test_speaking_desire_is_deterministic_and_personal() -> None:
    service = ViewerBehaviorService()
    viewer = _viewer()
    inputs = {
        "viewer": viewer,
        "persona": _persona(),
        "wave": _wave(),
        "assessment": _assessment(),
        "recent_speaker_count": 0,
        "crowd_pressure": 0.1,
        "session_seed": "session-seed",
    }

    first = service.evaluate(**inputs)
    repeated = service.evaluate(**inputs)
    next_revision = service.evaluate(
        **{**inputs, "viewer": viewer.model_copy(update={"behavior_revision": 2})}
    )

    assert repeated == first
    assert 0 < first.probability < 1
    assert first.draw != next_revision.draw
    assert first.probability == next_revision.probability


def test_direct_mention_forces_an_eligible_viewer_into_budget() -> None:
    service = ViewerBehaviorService()
    desire = service.evaluate(
        viewer=_viewer(),
        persona=_persona(),
        wave=_wave(target_viewer_id="viewer-1"),
        assessment=_assessment(),
        recent_speaker_count=3,
        crowd_pressure=1.0,
        session_seed="session-seed",
    )

    assert service.choose(
        [desire],
        maximum=1,
        forced_viewer_id="viewer-1",
    ) == ("viewer-1",)


def test_persona_mention_forces_one_eligible_instance_into_budget() -> None:
    service = ViewerBehaviorService()
    persona = _persona()
    target = service.evaluate(
        viewer=_viewer(),
        persona=persona,
        wave=_wave(),
        assessment=_assessment(),
        recent_speaker_count=3,
        crowd_pressure=1.0,
        session_seed="session-seed",
    )
    other = target.__class__(
        viewer_instance_id="viewer-2",
        persona_id="other-persona",
        eligible=True,
        reason="candidate",
        probability=1.0,
        draw=0.0,
        score=10.0,
    )

    assert service.choose(
        [target, other],
        maximum=1,
        forced_persona_id=persona.persona_id,
    ) == ("viewer-1",)


@pytest.mark.asyncio
async def test_viewer_revisions_fence_out_in_flight_results() -> None:
    viewer = _viewer()
    spec = _spec()
    pool = ViewerPoolSnapshot(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        mode_id="default",
        session_seed="session-seed",
        viewers=[viewer],
    )
    state = RuntimeStateStore()
    await state.activate(
        CommittedRuntime(
            session_id="session-1",
            spec=spec,
            audience_epoch=1,
            pool=pool,
        )
    )
    assert await state.claim_viewer_sequence(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        viewer_instance_id="viewer-1",
        viewer_sequence=1,
    )
    fence = {
        "room_id": "room-1",
        "session_id": "session-1",
        "audience_epoch": 1,
        "viewer_instance_id": "viewer-1",
        "viewer_sequence": 1,
        "presence_revision": 1,
        "moderation_revision": 1,
        "behavior_revision": 1,
    }
    assert await state.accepts(**fence)

    await state.update_viewer(
        session_id="session-1",
        viewer_instance_id="viewer-1",
        update=lambda value: value.model_copy(
            update={
                "lifecycle_state": ViewerLifecycleState.LEFT,
                "presence_revision": 2,
                "behavior_revision": 2,
                "last_left_at_ms": 2_000,
            }
        ),
    )

    assert not await state.accepts(**fence)


@pytest.mark.asyncio
async def test_mode_change_keeps_viewer_identity_and_sequence() -> None:
    viewer = _viewer().model_copy(update={"viewer_sequence": 1})
    spec = _spec()
    pool = ViewerPoolSnapshot(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        mode_id="default",
        session_seed="session-seed",
        viewers=[viewer],
    )
    state = RuntimeStateStore()
    await state.activate(
        CommittedRuntime(
            session_id="session-1",
            spec=spec,
            audience_epoch=1,
            pool=pool,
        )
    )

    next_mode = spec.modes[0].model_copy(
        update={"mode_id": "expanded", "namespace_id": "expanded", "revision": 2}
    )
    next_spec = spec.model_copy(
        update={
            "config_revision": 2,
            "active_mode_id": next_mode.mode_id,
            "modes": [next_mode],
        }
    )
    next_viewer = viewer.model_copy(update={"audience_epoch": 2})
    await state.replace(
        CommittedRuntime(
            session_id="session-1",
            spec=next_spec,
            audience_epoch=2,
            population_revision=2,
            pool=pool.model_copy(
                update={
                    "audience_epoch": 2,
                    "mode_id": next_mode.mode_id,
                    "viewers": [next_viewer],
                }
            ),
        )
    )

    snapshot = await state.snapshot("session-1")
    assert snapshot.pool.viewers[0].viewer_instance_id == "viewer-1"
    assert snapshot.pool.viewers[0].viewer_sequence == 1
    assert await state.claim_viewer_sequence(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=2,
        viewer_instance_id="viewer-1",
        viewer_sequence=2,
    )
