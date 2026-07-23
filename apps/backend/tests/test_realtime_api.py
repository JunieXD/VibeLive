from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.contracts.realtime import ClientHello
from advx_backend.domain.barrage import BarrageEvent
from advx_backend.main import create_app

LOCAL_TOKEN = "test-local-token"


def hello(*, token: str = LOCAL_TOKEN, protocol_version: int = 1) -> dict[str, object]:
    return {
        "type": "client.hello",
        "protocol_version": protocol_version,
        "token": token,
    }


def request_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        PROTOCOL_VERSION_HEADER: "1",
    }


def test_client_hello_does_not_reveal_local_token() -> None:
    message = ClientHello(protocol_version=1, token=LOCAL_TOKEN)

    assert LOCAL_TOKEN not in repr(message)


def test_realtime_handshake_stays_open_and_answers_ping(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            ready = websocket.receive_json()
            assert ready["type"] == "backend.ready"
            assert ready["protocol_version"] == 1
            assert ready["session"]["state"] == "idle"

            websocket.send_json(
                {
                    "type": "client.ping",
                    "protocol_version": 1,
                    "request_id": "ping-1",
                }
            )
            assert websocket.receive_json() == {
                "type": "backend.pong",
                "protocol_version": 1,
                "request_id": "ping-1",
            }


def test_realtime_broadcasts_ordered_http_session_changes(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()

            created = client.post("/sessions", headers=request_headers())
            assert created.status_code == 201

            starting = websocket.receive_json()
            running = websocket.receive_json()
            assert starting["type"] == "session.status"
            assert starting["session"]["state"] == "starting"
            assert running["session"]["state"] == "running"
            assert starting["session"]["revision"] < running["session"]["revision"]


@pytest.mark.parametrize(
    ("payload", "expected_code", "expected_close_code"),
    [
        (hello(token="wrong"), "authentication_failed", 4401),
        (hello(protocol_version=2), "version_mismatch", 4406),
    ],
)
def test_realtime_rejects_invalid_handshakes(
    payload: dict[str, object],
    expected_code: str,
    expected_close_code: int,
    tmp_path: Path,
) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(payload)
            error = websocket.receive_json()
            assert error["type"] == "protocol.error"
            assert error["code"] == expected_code
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == expected_close_code


def test_realtime_rejects_messages_outside_the_schema(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            websocket.send_json(
                {
                    "type": "client.unknown",
                    "protocol_version": 1,
                }
            )
            error = websocket.receive_json()
            assert error["code"] == "invalid_message"
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == 4400


def test_realtime_only_forwards_newer_session_revisions(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            ready = websocket.receive_json()
            initial_revision = ready["session"]["revision"]

            created = client.post("/sessions", headers=request_headers())
            assert created.status_code == 201
            first = websocket.receive_json()
            second = websocket.receive_json()

            assert first["session"]["revision"] > initial_revision
            assert second["session"]["revision"] > first["session"]["revision"]


def test_realtime_forwards_validated_barrage_events(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)
    event = BarrageEvent(
        barrage_id="barrage-1",
        session_id="session-1",
        observation_id="observation-1",
        request_id="request-1",
        audience_id="audience-1",
        text="hello",
        created_at_ms=100,
        expires_at_ms=200,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            assert client.portal is not None
            client.portal.call(runtime.realtime_broker.publish_barrage, event)

            message = websocket.receive_json()

    assert message == {
        "type": "barrage.event",
        "protocol_version": 1,
        "barrage": {
            "barrage_id": "barrage-1",
            "session_id": "session-1",
            "observation_id": "observation-1",
            "request_id": "request-1",
            "audience_id": "audience-1",
            "text": "hello",
            "created_at_ms": 100,
            "expires_at_ms": 200,
        },
    }
