from fastapi.testclient import TestClient

from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.main import create_app

LOCAL_TOKEN = "test-local-token"


def request_headers(
    *,
    token: str = LOCAL_TOKEN,
    protocol_version: str = "1",
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        PROTOCOL_VERSION_HEADER: protocol_version,
    }


def test_session_api_requires_local_token_and_protocol_version() -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN))

    with TestClient(app) as client:
        missing_token = client.post(
            "/sessions",
            headers={PROTOCOL_VERSION_HEADER: "1"},
        )
        missing_version = client.post(
            "/sessions",
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
        )
        wrong_version = client.post(
            "/sessions",
            headers=request_headers(protocol_version="2"),
        )

    assert missing_token.status_code == 401
    assert missing_token.json()["detail"]["code"] == "invalid_local_token"
    assert missing_version.status_code == 426
    assert missing_version.json()["detail"]["code"] == "protocol_version_mismatch"
    assert wrong_version.status_code == 426
    assert wrong_version.headers[PROTOCOL_VERSION_HEADER] == "1"


def test_session_api_runs_complete_lifecycle() -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN))

    with TestClient(app) as client:
        created = client.post("/sessions", headers=request_headers())
        assert created.status_code == 201
        running = created.json()
        session_id = running["session_id"]
        assert running["state"] == "running"
        assert running["revision"] == 2

        current = client.get("/sessions/current", headers=request_headers())
        assert current.json() == running

        paused = client.post(
            f"/sessions/{session_id}/pause",
            headers=request_headers(),
        )
        resumed = client.post(
            f"/sessions/{session_id}/resume",
            headers=request_headers(),
        )
        stopped = client.post(
            f"/sessions/{session_id}/stop",
            headers=request_headers(),
        )

        assert paused.json()["state"] == "paused"
        assert resumed.json()["state"] == "running"
        assert stopped.json()["state"] == "idle"
        assert stopped.json()["session_id"] is None
        assert stopped.json()["revision"] == 6


def test_session_api_distinguishes_conflicts_and_unknown_sessions() -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN))

    with TestClient(app) as client:
        created = client.post("/sessions", headers=request_headers())
        session_id = created.json()["session_id"]

        duplicate = client.post("/sessions", headers=request_headers())
        invalid_state = client.post(
            f"/sessions/{session_id}/resume",
            headers=request_headers(),
        )
        missing = client.post(
            "/sessions/not-current/pause",
            headers=request_headers(),
        )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "session_already_active"
    assert invalid_state.status_code == 409
    assert invalid_state.json()["detail"] == {
        "code": "invalid_session_state",
        "message": "cannot resume a running session; expected paused",
        "session_id": session_id,
        "state": "running",
        "allowed_states": ["paused"],
    }
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "session_not_found"
