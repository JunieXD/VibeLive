import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from advx_backend.api.ws.realtime import create_realtime_router
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.contracts.realtime import ClientHello
from advx_backend.domain.barrage import (
    BarrageEvent,
    BarrageEvidenceRef,
    BarrageEvidenceSource,
)
from advx_backend.main import create_app

LOCAL_TOKEN = "test-local-token"


def hello(*, token: str = LOCAL_TOKEN, protocol_version: int = 3) -> dict[str, object]:
    return {
        "type": "client.hello",
        "protocol_version": protocol_version,
        "token": token,
    }


def request_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        PROTOCOL_VERSION_HEADER: "3",
    }


def test_client_hello_does_not_reveal_local_token() -> None:
    message = ClientHello(protocol_version=3, token=LOCAL_TOKEN)

    assert LOCAL_TOKEN not in repr(message)


def test_realtime_handshake_stays_open_and_answers_ping(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            ready = websocket.receive_json()
            assert ready["type"] == "backend.ready"
            assert ready["protocol_version"] == 3
            assert ready["session"]["state"] == "idle"

            websocket.send_json(
                {
                    "type": "client.ping",
                    "protocol_version": 3,
                    "request_id": "ping-1",
                }
            )
            assert websocket.receive_json() == {
                "type": "backend.pong",
                "protocol_version": 3,
                "request_id": "ping-1",
            }


def test_realtime_broadcasts_ordered_session_changes(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()

            assert client.portal is not None
            client.portal.call(runtime.session_service.start)

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
        (hello(protocol_version=1), "version_mismatch", 4406),
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
                    "protocol_version": 3,
                }
            )
            error = websocket.receive_json()
            assert error["code"] == "invalid_message"
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == 4400


def test_realtime_rejects_v1_after_a_successful_v2_handshake(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            websocket.send_json(
                {
                    "type": "client.ping",
                    "protocol_version": 1,
                    "request_id": "stale-client",
                }
            )
            error = websocket.receive_json()
            assert error == {
                "type": "protocol.error",
                "protocol_version": 3,
                "code": "version_mismatch",
                "message": "The requested protocol version is not supported.",
                "supported_version": 3,
            }
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == 4406


def test_realtime_only_forwards_newer_session_revisions(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            ready = websocket.receive_json()
            initial_revision = ready["session"]["revision"]

            assert client.portal is not None
            client.portal.call(runtime.session_service.start)
            first = websocket.receive_json()
            second = websocket.receive_json()

            assert first["session"]["revision"] > initial_revision
            assert second["session"]["revision"] > first["session"]["revision"]


def test_realtime_forwards_validated_barrage_events(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)
    event = BarrageEvent(
        barrage_id="barrage-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        observation_id="observation-1",
        generation_request_id="request-1",
        viewer_instance_id="viewer-1",
        persona_id="persona-1",
        display_name="Viewer One",
        viewer_sequence=1,
        reaction_type="reply",
        evidence_refs=(
            BarrageEvidenceRef(
                source=BarrageEvidenceSource.EVENT,
                event_id="event-1",
            ),
        ),
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
        "protocol_version": 3,
        "barrage": {
            "barrage_id": "barrage-1",
            "room_id": "room-1",
            "session_id": "session-1",
            "audience_epoch": 1,
            "observation_id": "observation-1",
            "generation_request_id": "request-1",
            "viewer_instance_id": "viewer-1",
            "persona_id": "persona-1",
            "display_name": "Viewer One",
            "viewer_sequence": 1,
            "reaction_type": "reply",
            "intent": "react_to_scene",
            "target": None,
            "evidence_refs": [
                {
                    "source": "event",
                    "event_id": "event-1",
                    "frame_index": None,
                }
            ],
            "text": "hello",
            "created_at_ms": 100,
            "expires_at_ms": 200,
        },
    }


@pytest.mark.asyncio
async def test_realtime_cancellation_completes_connection_cleanup(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    ready = asyncio.Event()
    block_receive = asyncio.Event()

    class BlockingWebSocket:
        def __init__(self) -> None:
            self.received_hello = False

        async def accept(self) -> None:
            return None

        async def receive(self) -> dict[str, object]:
            if not self.received_hello:
                self.received_hello = True
                return {
                    "type": "websocket.receive",
                    "text": json.dumps(hello()),
                }
            await block_receive.wait()
            raise AssertionError("cancelled receive unexpectedly resumed")

        async def send_json(self, message: dict[str, object]) -> None:
            if message["type"] == "backend.ready":
                ready.set()

    router = create_realtime_router(
        session_service=runtime.session_service,
        broker=runtime.realtime_broker,
        ingest_gateway=runtime.ingest_gateway,
        local_token=runtime.local_token,
    )
    endpoint = router.routes[0].endpoint
    connection = asyncio.create_task(endpoint(BlockingWebSocket()))
    await asyncio.wait_for(ready.wait(), timeout=1)

    connection.cancel()
    await asyncio.wait_for(connection, timeout=1)

    assert not runtime.realtime_broker._subscribers
    assert not runtime.realtime_broker._barrage_subscribers
