from fastapi.testclient import TestClient

from advx_backend.application.runtime_session_service import NoOpRuntimeCapabilityProbe
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.configuration import ProviderConfigurationRequest
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.contracts.session import RuntimeSessionStartRequest
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
    RuntimeApplyRequest,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.main import create_app

LOCAL_TOKEN = "viewer-audience-token"


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        PROTOCOL_VERSION_HEADER: "3",
    }


def spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
        persona_id="curious",
        document_version=1,
        revision=1,
        content_hash="1" * 64,
        display_name="好奇型",
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
        persona_counts={persona.persona_id: 2},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=0, maximum=2),
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


def test_moderation_and_new_session_identity(tmp_path) -> None:
    runtime = build_runtime(
        local_token=LOCAL_TOKEN,
        data_directory=tmp_path,
        runtime_capability_probe=NoOpRuntimeCapabilityProbe(),
    )
    canonical = spec()
    runtime.provider_controller.install_initial(
        ProviderConfigurationRequest(
            provider_profile_id="provider-1",
            model_base_url="https://models.example/v1",
            model_name="viewer",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="visual",
            model_api_key="model-key",
            asr_api_key="asr-key",
        )
    )

    with TestClient(create_app(runtime=runtime)) as client:
        first = _start(client, canonical, "start-1")
        first_session_id = first["session_id"]
        audience = client.get(
            f"/runtime/sessions/{first_session_id}/audience",
            headers=headers(),
        ).json()
        assert audience["active_count"] == 2
        first_ids = {viewer["viewer_instance_id"] for viewer in audience["viewers"]}
        assert all(viewer["display_name"] != "好奇型" for viewer in audience["viewers"])

        viewer_id = audience["viewers"][0]["viewer_instance_id"]
        muted = client.post(
            f"/runtime/sessions/{first_session_id}/viewers/{viewer_id}/mute",
            headers=headers(),
            json={"command_id": "mute-1", "duration_ms": 60_000},
        )
        assert muted.status_code == 200
        assert muted.json()["muted_until_ms"] is not None

        conflicting_mute = client.post(
            f"/runtime/sessions/{first_session_id}/viewers/"
            f"{audience['viewers'][1]['viewer_instance_id']}/mute",
            headers=headers(),
            json={"command_id": "mute-1", "duration_ms": 60_000},
        )
        assert conflicting_mute.status_code == 409
        assert conflicting_mute.json()["detail"]["code"] == "viewer_state_conflict"

        unmuted = client.post(
            f"/runtime/sessions/{first_session_id}/viewers/{viewer_id}/unmute",
            headers=headers(),
            json={"command_id": "unmute-1"},
        )
        assert unmuted.status_code == 200
        assert unmuted.json()["muted_until_ms"] is None

        kicked = client.post(
            f"/runtime/sessions/{first_session_id}/viewers/{viewer_id}/kick",
            headers=headers(),
            json={"command_id": "kick-1", "reason": "host moderation"},
        )
        assert kicked.status_code == 200
        assert kicked.json()["presence_state"] == "kicked"
        after_kick = client.get(
            f"/runtime/sessions/{first_session_id}/audience",
            headers=headers(),
        ).json()
        assert after_kick["active_count"] == 2
        assert len(after_kick["viewers"]) == 3

        switched_mode = ModeDefinition(
            mode_id="expanded",
            namespace_id="expanded",
            revision=2,
            persona_counts={canonical.personas[0].persona_id: 3},
            normal_response_range=ResponseRange(minimum=0, maximum=2),
            highlight_response_range=ResponseRange(minimum=0, maximum=3),
        )
        updated = canonical.model_copy(
            update={
                "config_revision": 2,
                "active_mode_id": switched_mode.mode_id,
                "modes": [*canonical.modes, switched_mode],
            }
        )
        apply_request = RuntimeApplyRequest(
            apply_id="apply-expanded",
            base_revision=1,
            canonical_runtime_spec=updated,
            client_config_hash=updated.config_hash(),
        )
        applied = client.post(
            f"/runtime/sessions/{first_session_id}/apply",
            headers=headers(),
            json=apply_request.model_dump(mode="json"),
        )
        assert applied.status_code == 200, applied.text
        after_apply = client.get(
            f"/runtime/sessions/{first_session_id}/audience",
            headers=headers(),
        ).json()
        assert after_apply["population_revision"] > after_kick["population_revision"]
        assert after_apply["target_concurrent_viewers"] == 3
        assert after_apply["active_count"] == 3
        assert len(after_apply["viewers"]) == 4
        assert {
            viewer["viewer_instance_id"] for viewer in after_kick["viewers"]
        }.issubset(
            {
                viewer["viewer_instance_id"]
                for viewer in after_apply["viewers"]
            }
        )

        stopped = client.post(
            f"/sessions/{first_session_id}/stop",
            headers=headers(),
        )
        assert stopped.status_code == 200
        stopped_audience = client.get(
            f"/runtime/sessions/{first_session_id}/audience",
            headers=headers(),
        ).json()
        assert stopped_audience["active_count"] == 0
        assert stopped_audience["viewers"] == []
        second = _start(client, canonical, "start-2")
        second_audience = client.get(
            f"/runtime/sessions/{second['session_id']}/audience",
            headers=headers(),
        ).json()
        second_ids = {
            viewer["viewer_instance_id"] for viewer in second_audience["viewers"]
        }
        assert first_ids.isdisjoint(second_ids)


def _start(
    client: TestClient,
    canonical: CanonicalRuntimeSpec,
    request_id: str,
) -> dict[str, object]:
    request = RuntimeSessionStartRequest(
        client_request_id=request_id,
        canonical_runtime_spec=canonical,
        client_config_hash=canonical.config_hash(),
    )
    response = client.post(
        "/runtime/sessions",
        headers=headers(),
        json=request.model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    return response.json()
