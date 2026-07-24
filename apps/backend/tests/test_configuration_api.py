from pathlib import Path

from fastapi.testclient import TestClient

from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.protocol import PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from advx_backend.main import create_app
from advx_backend.providers.model.base import (
    CapabilityProbeCheck,
    CapabilityProbeResult,
    CapabilityProbeStatus,
)

LOCAL_TOKEN = "test-local-token"


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        PROTOCOL_VERSION_HEADER: str(PROTOCOL_VERSION),
    }


def provider_payload(*, model_name: str = "test-model") -> dict[str, str]:
    return {
        "model_base_url": "https://models.example/v1",
        "model_name": model_name,
        "model_api_key": "private-model-key",
        "asr_api_key": "private-asr-key",
    }


def test_provider_configuration_is_authenticated_idempotent_and_secret_safe(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        missing_auth = client.get("/configuration/providers")
        initial = client.get("/configuration/providers", headers=headers())
        configured = client.put(
            "/configuration/providers",
            headers=headers(),
            json=provider_payload(),
        )
        configured_again = client.put(
            "/configuration/providers",
            headers=headers(),
            json=provider_payload(),
        )

    assert missing_auth.status_code == 401
    assert initial.json() == {
        "configured": False,
        "provider_profile_id": None,
        "model_base_url": None,
        "model_name": None,
        "director_model": None,
        "viewer_model": None,
        "memory_model": None,
        "visual_summary_model": None,
        "asr_model": None,
    }
    assert configured.status_code == 200
    assert configured.json() == {
        "configured": True,
        "provider_profile_id": "default",
        "model_base_url": "https://models.example/v1",
        "model_name": "test-model",
        "director_model": "test-model",
        "viewer_model": "test-model",
        "memory_model": "test-model",
        "visual_summary_model": "test-model",
        "asr_model": "stepaudio-2.5-asr",
    }
    assert configured_again.json() == configured.json()
    assert "private-model-key" not in repr(runtime)
    assert "private-asr-key" not in repr(runtime)


def test_provider_configuration_rejects_replacement_and_active_session(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        configured = client.put(
            "/configuration/providers",
            headers=headers(),
            json=provider_payload(),
        )
        replacement = client.put(
            "/configuration/providers",
            headers=headers(),
            json=provider_payload(model_name="different-model"),
        )
        assert client.portal is not None
        session = client.portal.call(runtime.session_service.start)
        active = client.put(
            "/configuration/providers",
            headers=headers(),
            json=provider_payload(),
        )
        assert session.session_id is not None
        client.post(f"/sessions/{session.session_id}/stop", headers=headers())

    assert configured.status_code == 200
    assert replacement.status_code == 409
    assert replacement.json()["detail"]["code"] == "providers_already_configured"
    assert active.status_code == 409
    assert active.json()["detail"]["code"] == "session_active"


def test_provider_role_models_and_redacted_capability_endpoints(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeProvider:
        async def discover_models(self) -> tuple[str, ...]:
            return ("director-v1", "viewer-v1")

        async def probe_capabilities(
            self,
            *,
            role_models: dict[str, str],
        ) -> CapabilityProbeResult:
            assert role_models == {
                "director": "director-v1",
                "viewer": "viewer-v1",
                "memory": "shared-v1",
                "visual_summary": "shared-v1",
            }
            return CapabilityProbeResult(
                status=CapabilityProbeStatus.PASSED,
                discovered_model_ids=("director-v1", "viewer-v1"),
                checks=(
                    CapabilityProbeCheck(
                        capability="director_structured_output",
                        status=CapabilityProbeStatus.PASSED,
                        model_id="director-v1",
                    ),
                ),
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "advx_backend.api.http.configuration._provider",
        lambda request, model_id: FakeProvider(),
    )
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)
    payload = provider_payload(model_name="shared-v1") | {
        "provider_profile_id": "active-profile",
        "director_model": "director-v1",
        "viewer_model": "viewer-v1",
    }

    with TestClient(app) as client:
        configured = client.put(
            "/configuration/providers",
            headers=headers(),
            json=payload,
        )
        models = client.get("/configuration/providers/models", headers=headers())
        probe = client.post("/configuration/providers/probe", headers=headers())

    assert configured.json()["provider_profile_id"] == "active-profile"
    assert configured.json()["director_model"] == "director-v1"
    assert configured.json()["viewer_model"] == "viewer-v1"
    assert configured.json()["memory_model"] == "shared-v1"
    assert models.json() == {
        "provider_profile_id": "active-profile",
        "model_ids": ["director-v1", "viewer-v1"],
    }
    assert probe.status_code == 200
    assert probe.json()["status"] == "passed"
    assert probe.json()["checks"][-1] == {
        "capability": "asr_adapter",
        "status": "skipped",
        "model_id": "stepaudio-2.5-asr",
        "error_code": "requires_final_audio",
        "http_status": None,
    }
    serialized = probe.text + models.text + configured.text
    assert "private-model-key" not in serialized
    assert "private-asr-key" not in serialized


def test_provider_discovery_requires_an_active_profile(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        response = client.get("/configuration/providers/models", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "providers_not_configured"
