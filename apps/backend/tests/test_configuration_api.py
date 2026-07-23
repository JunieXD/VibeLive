from pathlib import Path

from fastapi.testclient import TestClient

from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.main import create_app

LOCAL_TOKEN = "test-local-token"


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        PROTOCOL_VERSION_HEADER: "1",
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
        "model_base_url": None,
        "model_name": None,
        "asr_model": None,
    }
    assert configured.status_code == 200
    assert configured.json() == {
        "configured": True,
        "model_base_url": "https://models.example/v1",
        "model_name": "test-model",
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
        session = client.post("/sessions", headers=headers()).json()
        active = client.put(
            "/configuration/providers",
            headers=headers(),
            json=provider_payload(),
        )
        client.post(f"/sessions/{session['session_id']}/stop", headers=headers())

    assert configured.status_code == 200
    assert replacement.status_code == 409
    assert replacement.json()["detail"]["code"] == "providers_already_configured"
    assert active.status_code == 409
    assert active.json()["detail"]["code"] == "session_active"
