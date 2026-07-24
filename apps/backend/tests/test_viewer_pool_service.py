from advx_backend.application.viewer_pool_service import ViewerPoolService
from advx_backend.contracts.viewer_runtime import CanonicalRuntimeSpec, ProviderRuntimeSpec, Room
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange


class _UnusedIdGenerator:
    def new_id(self) -> str:
        raise AssertionError("Viewer identity must be derived from the Session seed")


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
        target_concurrent_viewers=target,
        persona_ids=[persona.persona_id],
        persona_weights={persona.persona_id: 1},
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
