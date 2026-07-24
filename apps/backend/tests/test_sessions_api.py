from pathlib import Path

from fastapi.testclient import TestClient

from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.main import create_app

LOCAL_TOKEN = "test-local-token"


def request_headers(
    *,
    token: str = LOCAL_TOKEN,
    protocol_version: str = "2",
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        PROTOCOL_VERSION_HEADER: protocol_version,
    }


def test_session_api_requires_local_token_and_protocol_version(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        missing_token = client.post(
            "/sessions",
            headers={PROTOCOL_VERSION_HEADER: "2"},
        )
        missing_version = client.post(
            "/sessions",
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
        )
        wrong_version = client.post(
            "/sessions",
            headers=request_headers(protocol_version="1"),
        )

    assert missing_token.status_code == 401
    assert missing_token.json()["detail"]["code"] == "invalid_local_token"
    assert missing_version.status_code == 426
    assert missing_version.json()["detail"]["code"] == "protocol_version_mismatch"
    assert wrong_version.status_code == 426
    assert wrong_version.headers[PROTOCOL_VERSION_HEADER] == "2"


def test_session_api_requires_canonical_runtime_start(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        created = client.post("/sessions", headers=request_headers())
        assert created.status_code == 409
        assert created.json()["detail"]["code"] == "runtime_snapshot_required"
        current = client.get("/sessions/current", headers=request_headers())
        assert current.json()["state"] == "idle"
        assert current.json()["session_id"] is None


def test_session_api_distinguishes_conflicts_and_unknown_sessions(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        legacy_start = client.post("/sessions", headers=request_headers())
        missing_resume = client.post(
            "/sessions/not-current/resume",
            headers=request_headers(),
        )
        missing = client.post(
            "/sessions/not-current/pause",
            headers=request_headers(),
        )

    assert legacy_start.status_code == 409
    assert legacy_start.json()["detail"]["code"] == "runtime_snapshot_required"
    assert missing_resume.status_code == 404
    assert missing_resume.json()["detail"]["code"] == "session_not_found"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "session_not_found"
