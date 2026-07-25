from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec, ProviderRuntimeSpec, Room
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.viewer import ViewerLifecycleState


class _UnusedIdGenerator:
    def new_id(self) -> str:
        raise AssertionError("Viewer identity must be derived from the Session seed")


def test_legacy_weighted_mode_configuration_migrates_to_exact_persona_counts() -> None:
    mode = ModeDefinition.model_validate(
        {
            "mode_id": "legacy",
            "namespace_id": "legacy",
            "revision": 1,
            "target_concurrent_viewers": 5,
            "persona_ids": ["first", "second", "disabled"],
            "persona_weights": {"first": 1, "second": 2, "disabled": 0},
            "persona_overrides": {},
            "normal_response_range": {"minimum": 0, "maximum": 2},
            "highlight_response_range": {"minimum": 1, "maximum": 5},
            "ambience": "natural",
        }
    )

    assert mode.persona_counts == {"first": 2, "second": 3, "disabled": 0}
    assert mode.viewer_count == 5
    assert mode.model_dump(exclude_none=True) == {
        "mode_id": "legacy",
        "namespace_id": "legacy",
        "revision": 1,
        "persona_counts": {"first": 2, "second": 3, "disabled": 0},
        "persona_overrides": {},
        "normal_response_range": {"minimum": 0, "maximum": 2},
        "highlight_response_range": {"minimum": 1, "maximum": 5},
        "ambience": "natural",
    }


def test_replacement_uses_persisted_creation_ordinal_after_removed_viewers() -> None:
    service = ViewerPoolService(id_generator=_UnusedIdGenerator())
    spec = _spec(target=5)
    original = service.create_pool(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        session_seed="session-1",
        spec=spec,
    )
    recovered = original.model_copy(
        update={
            "viewers": original.viewers[:2],
            "next_creation_ordinal": 6,
        }
    )

    replacement = service.create_replacement(
        current=recovered,
        spec=spec,
        created_at_ms=2,
    )

    assert replacement.ordinal == 6
    assert replacement.viewer_instance_id not in {
        viewer.viewer_instance_id for viewer in original.viewers
    }


def test_persona_counts_are_exact_for_pool_reconciliation_and_replacements() -> None:
    service = ViewerPoolService(id_generator=_UnusedIdGenerator())
    initial = _two_persona_spec(curious_count=2, skeptic_count=1, revision=1)
    pool = service.create_pool(
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        session_seed="session-1",
        spec=initial,
    )

    assert _active_counts(pool) == {"curious": 2, "skeptic": 1}

    next_spec = _two_persona_spec(curious_count=1, skeptic_count=2, revision=2)
    reconciliation = service.reconcile(current=pool, next_epoch=2, spec=next_spec)

    assert _active_counts(reconciliation.snapshot) == {"curious": 1, "skeptic": 2}
    assert len(reconciliation.retained_viewer_ids) == 2
    assert len(reconciliation.reset_viewer_ids) == 1
    assert reconciliation.added_viewer_ids == ()
    assert reconciliation.removed_viewer_ids == ()

    departed = next(
        viewer
        for viewer in reconciliation.snapshot.viewers
        if viewer.is_active() and viewer.persona_id == "skeptic"
    ).model_copy(update={"lifecycle_state": ViewerLifecycleState.LEFT})
    reduced = reconciliation.snapshot.model_copy(
        update={
            "viewers": [
                departed if viewer.viewer_instance_id == departed.viewer_instance_id else viewer
                for viewer in reconciliation.snapshot.viewers
            ]
        }
    )

    replacement = service.create_replacement(
        current=reduced,
        spec=next_spec,
        created_at_ms=2,
    )

    assert replacement.persona_id == "skeptic"


def _spec(*, target: int) -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="curious",
        document_version=1,
        revision=1,
        content_hash="1" * 64,
        display_name="Curious",
        role="curious viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.1,
        cooldown_ms=1_000,
    )
    mode = ModeDefinition(
        mode_id="default",
        namespace_id="default",
        revision=1,
        persona_counts={persona.persona_id: target},
        normal_response_range=ResponseRange(minimum=0, maximum=2),
        highlight_response_range=ResponseRange(minimum=0, maximum=5),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
        ),
        active_mode_id=mode.mode_id,
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


def _two_persona_spec(
    *,
    curious_count: int,
    skeptic_count: int,
    revision: int,
) -> CanonicalRuntimeSpec:
    curious = PersonaTemplate(
        persona_id="curious",
        document_version=1,
        revision=1,
        content_hash="1" * 64,
        display_name="Curious",
        role="curious viewer",
        silence_bias=0.2,
        burst_bias=0.2,
        repetition_bias=0.1,
        cooldown_ms=1_000,
    )
    skeptic = curious.model_copy(
        update={
            "persona_id": "skeptic",
            "content_hash": "2" * 64,
            "display_name": "Skeptic",
            "role": "skeptical viewer",
        }
    )
    mode = ModeDefinition(
        mode_id="default",
        namespace_id="default",
        revision=revision,
        persona_counts={"curious": curious_count, "skeptic": skeptic_count},
        normal_response_range=ResponseRange(minimum=0, maximum=2),
        highlight_response_range=ResponseRange(minimum=0, maximum=3),
    )
    return CanonicalRuntimeSpec(
        config_revision=revision,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=1,
            updated_at_ms=1,
        ),
        active_mode_id=mode.mode_id,
        personas=[curious, skeptic],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
        ),
    )


def _active_counts(pool: object) -> dict[str, int]:
    viewers = getattr(pool, "viewers")
    persona_ids = [viewer.persona_id for viewer in viewers if viewer.is_active()]
    return {
        persona_id: persona_ids.count(persona_id)
        for persona_id in sorted(set(persona_ids))
    }
