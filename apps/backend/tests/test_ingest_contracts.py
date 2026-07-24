from dataclasses import fields

import pytest

from advx_backend.application.ports.ingest import (
    AudioCommit,
    AudioInput,
    FrameInput,
    FrameStoreLimits,
    IngestInputKind,
    IngestReceipt,
    IngestReceiptStage,
    ResolvedFrame,
    TextInput,
)
from advx_backend.contracts.realtime import (
    BackendPong,
    ClientAudioCommit,
    ClientHello,
    ClientMessageEnvelope,
    ClientPing,
    ClientTextSubmit,
    IngestAck,
    IngestRejected,
    ServerMessageEnvelope,
)
from advx_backend.domain.observation import FrameRef, Observation


def test_application_ingest_dtos_hide_media_bodies_and_observations_hold_refs() -> None:
    secret = b"private-frame-pixels"
    audio = AudioInput(
        session_id="session-1",
        input_id="audio-1",
        captured_at_ms=100,
        format="audio/pcm;rate=16000;channels=1;format=s16le",
        body=secret,
    )
    frame = FrameInput(
        session_id="session-1",
        input_id="frame-1",
        captured_at_ms=100,
        mime_type="image/webp",
        body=secret,
    )
    resolved = ResolvedFrame(
        session_id="session-1",
        frame_id="frame-1",
        input_id="frame-1",
        captured_at_ms=100,
        mime_type="image/webp",
        body=secret,
    )

    assert secret.decode() not in repr(audio)
    assert secret.decode() not in repr(frame)
    assert secret.decode() not in repr(resolved)
    assert "body" not in {field.name for field in fields(FrameRef)}
    assert "body" not in {field.name for field in fields(Observation)}


def test_application_ingest_dtos_capture_each_input_boundary() -> None:
    text = TextInput(
        session_id="session-1",
        input_id="text-1",
        created_at_ms=100,
        text="hello",
    )
    commit = AudioCommit(
        session_id="session-1",
        input_id="audio-1",
        committed_at_ms=200,
    )
    receipt = IngestReceipt(
        session_id="session-1",
        input_id="audio-1",
        input_kind="audio",
        stage="committed",
        accepted_at_ms=201,
    )

    assert text.text == "hello"
    assert "hello" not in repr(text)
    assert commit.committed_at_ms == 200
    assert receipt.input_kind is IngestInputKind.AUDIO
    assert receipt.stage is IngestReceiptStage.COMMITTED
    limits = FrameStoreLimits(max_frames=4, max_frame_bytes=1_024, max_total_bytes=2_048)
    assert limits.max_frames == 4


def test_frame_input_strips_validated_change_score_from_mime_metadata() -> None:
    frame = FrameInput(
        session_id="session-1",
        input_id="frame-1",
        captured_at_ms=100,
        mime_type="IMAGE/JPEG;advx-change-score=0.125679",
        body=b"encoded",
    )

    assert frame.mime_type == "image/jpeg"
    assert frame.change_score == pytest.approx(0.125679)


@pytest.mark.parametrize(
    "mime_type",
    [
        "image/jpeg;advx-change-score=",
        "image/jpeg;advx-change-score=nan",
        "image/jpeg;advx-change-score=-0.1",
        "image/jpeg;advx-change-score=1.1",
        "image/jpeg;quality=80",
        "image/jpeg;advx-change-score=0.5;quality=80",
    ],
)
def test_frame_input_rejects_invalid_change_score_metadata(mime_type: str) -> None:
    with pytest.raises(ValueError):
        FrameInput(
            session_id="session-1",
            input_id="frame-1",
            captured_at_ms=100,
            mime_type=mime_type,
            body=b"encoded",
        )


def test_realtime_ingest_messages_are_additive_to_existing_client_messages() -> None:
    hello = ClientMessageEnvelope.model_validate(
        {
            "type": "client.hello",
            "protocol_version": 2,
            "token": "local-token",
        }
    ).root
    ping = ClientMessageEnvelope.model_validate(
        {
            "type": "client.ping",
            "protocol_version": 2,
            "request_id": "ping-1",
        }
    ).root
    text = ClientMessageEnvelope.model_validate(
        {
            "type": "client.text.submit",
            "protocol_version": 2,
            "session_id": "session-1",
            "input_id": "text-1",
            "created_at_ms": 100,
            "text": "hello",
        }
    ).root
    commit = ClientMessageEnvelope.model_validate(
        {
            "type": "client.audio.commit",
            "protocol_version": 2,
            "session_id": "session-1",
            "input_id": "audio-1",
            "committed_at_ms": 200,
        }
    ).root

    assert isinstance(hello, ClientHello)
    assert isinstance(ping, ClientPing)
    assert isinstance(text, ClientTextSubmit)
    assert isinstance(commit, ClientAudioCommit)


def test_realtime_server_messages_include_ingest_ack_and_rejection_without_changing_pong() -> None:
    pong = ServerMessageEnvelope.model_validate(
        {
            "type": "backend.pong",
            "protocol_version": 2,
            "request_id": "ping-1",
        }
    ).root
    acknowledgement = ServerMessageEnvelope.model_validate(
        {
            "type": "ingest.ack",
            "protocol_version": 2,
            "session_id": "session-1",
            "input_id": "audio-1",
            "input_kind": "audio",
            "stage": "committed",
            "accepted_at_ms": 201,
        }
    ).root
    rejected = ServerMessageEnvelope.model_validate(
        {
            "type": "ingest.rejected",
            "protocol_version": 2,
            "code": "payload_too_large",
            "message": "The audio payload is too large.",
            "session_id": "session-1",
            "input_id": "audio-2",
            "input_kind": "audio",
        }
    ).root

    assert isinstance(pong, BackendPong)
    assert isinstance(acknowledgement, IngestAck)
    assert isinstance(rejected, IngestRejected)
