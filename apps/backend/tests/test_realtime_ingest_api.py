from pathlib import Path

from fastapi.testclient import TestClient

from advx_backend.application.ingest_service import IngestSessionNotActiveError
from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    IngestInputKind,
    IngestReceipt,
    IngestReceiptStage,
    TextInput,
)
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.binary import (
    BinaryEnvelopeHeader,
    BinaryInputEnvelope,
    BinaryMediaType,
    encode_binary_envelope,
)
from advx_backend.main import create_app

LOCAL_TOKEN = "test-local-token"


def hello() -> dict[str, object]:
    return {
        "type": "client.hello",
        "protocol_version": 3,
        "token": LOCAL_TOKEN,
    }


def envelope(
    *,
    media_type: BinaryMediaType,
    input_id: str,
    format_value: str,
    body: bytes,
) -> bytes:
    return encode_binary_envelope(
        BinaryInputEnvelope(
            header=BinaryEnvelopeHeader(
                media_type=media_type,
                session_id="session-1",
                input_id=input_id,
                captured_at_ms=100,
                format=format_value,
                body_length=len(body),
            ),
            body=body,
        )
    )


class RecordingIngestPort:
    def __init__(self, *, reject_inactive: bool = False) -> None:
        self.reject_inactive = reject_inactive
        self.inputs: list[TextInput | AudioInput | AudioCommit | FrameInput] = []

    async def submit_text(self, input: TextInput) -> IngestReceipt:
        return self._record(input, IngestInputKind.TEXT)

    async def submit_audio(self, input: AudioInput) -> IngestReceipt:
        return self._record(input, IngestInputKind.AUDIO)

    async def commit_audio(self, commit: AudioCommit) -> IngestReceipt:
        return self._record(
            commit,
            IngestInputKind.AUDIO,
            stage=IngestReceiptStage.COMMITTED,
        )

    async def submit_frame(self, input: FrameInput) -> IngestReceipt:
        return self._record(input, IngestInputKind.FRAME)

    def _record(
        self,
        input: TextInput | AudioInput | AudioCommit | FrameInput,
        kind: IngestInputKind,
        *,
        stage: IngestReceiptStage = IngestReceiptStage.RECEIVED,
    ) -> IngestReceipt:
        if self.reject_inactive:
            raise IngestSessionNotActiveError(input.session_id, None)
        self.inputs.append(input)
        return IngestReceipt(
            session_id=input.session_id,
            input_id=input.input_id,
            input_kind=kind,
            stage=stage,
            accepted_at_ms=123,
        )
def test_realtime_dispatches_binary_audio_frame_and_audio_commit(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    ingest = RecordingIngestPort()
    runtime.ingest_gateway.configure(ingest)
    app = create_app(runtime=runtime)
    audio_body = b"\x00\x00\x01\x00"
    frame_body = b"private-frame"

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            websocket.send_bytes(
                envelope(
                    media_type=BinaryMediaType.AUDIO,
                    input_id="audio-1",
                    format_value="audio/pcm;rate=16000;channels=1;format=s16le",
                    body=audio_body,
                )
            )
            assert websocket.receive_json()["input_kind"] == "audio"

            websocket.send_json(
                {
                    "type": "client.audio.commit",
                    "protocol_version": 3,
                    "session_id": "session-1",
                    "input_id": "audio-1",
                    "committed_at_ms": 101,
                }
            )
            assert websocket.receive_json()["stage"] == "committed"

            websocket.send_bytes(
                envelope(
                    media_type=BinaryMediaType.IMAGE,
                    input_id="frame-1",
                    format_value="image/webp;advx-change-score=0.375",
                    body=frame_body,
                )
            )
            assert websocket.receive_json()["input_kind"] == "frame"

    assert isinstance(ingest.inputs[0], AudioInput)
    assert ingest.inputs[0].body == audio_body
    assert isinstance(ingest.inputs[1], AudioCommit)
    assert isinstance(ingest.inputs[2], FrameInput)
    assert ingest.inputs[2].body == frame_body
    assert ingest.inputs[2].mime_type == "image/webp"
    assert ingest.inputs[2].change_score == 0.375


def test_realtime_forwards_text_target(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    ingest = RecordingIngestPort()
    runtime.ingest_gateway.configure(ingest)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            websocket.send_json(
                {
                    "type": "client.text.submit",
                    "protocol_version": 3,
                    "session_id": "session-1",
                    "input_id": "text-1",
                    "created_at_ms": 100,
                    "text": "hello",
                    "target_viewer_id": "viewer-1",
                }
            )
            assert websocket.receive_json()["input_kind"] == "text"

    assert len(ingest.inputs) == 1
    assert isinstance(ingest.inputs[0], TextInput)
    assert ingest.inputs[0].target_viewer_id == "viewer-1"
    assert ingest.inputs[0].target_persona_id is None
def test_realtime_rejects_unavailable_and_inactive_ingest(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)
    message = {
        "type": "client.text.submit",
        "protocol_version": 3,
        "session_id": "session-1",
        "input_id": "text-1",
        "created_at_ms": 100,
        "text": "private text",
    }

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            websocket.send_json(message)
            unavailable = websocket.receive_json()

    assert unavailable["code"] == "pipeline_unavailable"
    assert "private text" not in unavailable["message"]

    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path / "inactive")
    runtime.ingest_gateway.configure(RecordingIngestPort(reject_inactive=True))
    app = create_app(runtime=runtime)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            websocket.send_json(message)
            inactive = websocket.receive_json()

    assert inactive["code"] == "session_not_active"
    assert inactive["session_id"] == "session-1"


def test_realtime_rejects_malformed_binary_without_closing_connection(tmp_path: Path) -> None:
    runtime = build_runtime(local_token=LOCAL_TOKEN, data_directory=tmp_path)
    app = create_app(runtime=runtime)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(hello())
            websocket.receive_json()
            websocket.send_bytes(b"private malformed media")
            rejected = websocket.receive_json()
            websocket.send_json(
                {
                    "type": "client.ping",
                    "protocol_version": 3,
                    "request_id": "after-rejection",
                }
            )
            pong = websocket.receive_json()

    assert rejected["code"] == "malformed_binary_envelope"
    assert "private malformed media" not in rejected["message"]
    assert pong["type"] == "backend.pong"
