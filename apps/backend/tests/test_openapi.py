from advx_backend.bootstrap import build_runtime
from advx_backend.main import create_app


def test_openapi_includes_http_and_realtime_contracts() -> None:
    app = create_app(runtime=build_runtime(local_token="test-local-token"))

    schema = app.openapi()
    schemas = schema["components"]["schemas"]

    assert "/sessions" in schema["paths"]
    assert "/sessions/{session_id}/pause" in schema["paths"]
    assert "ClientMessageEnvelope" in schemas
    assert "ServerMessageEnvelope" in schemas
    assert "BarrageEventMessage" in schemas
    assert "BarrageSnapshot" in schemas
    assert "BinaryEnvelopeHeader" in schemas
    assert "ClientTextSubmit" in schemas
    assert "ClientAudioCommit" in schemas
    assert "IngestAck" in schemas
    assert "IngestRejected" in schemas
    assert "SessionStartRequest" in schemas
    assert "SessionStartResponse" in schemas
    assert "PersonaMemoryRevisionCommitted" in schemas
    assert "ModeMemeChanged" in schemas
    start_parameters = schema["paths"]["/sessions"]["post"]["parameters"]
    version_header = next(
        parameter
        for parameter in start_parameters
        if parameter["name"] == "X-ADVX-Protocol-Version"
    )
    assert version_header["required"] is True
    assert version_header["schema"]["const"] == "2"
    assert schema["x-advx-realtime"] == {
        "path": "/ws",
        "protocolVersion": 2,
        "clientMessage": {
            "$ref": "#/components/schemas/ClientMessageEnvelope",
        },
        "serverMessage": {
            "$ref": "#/components/schemas/ServerMessageEnvelope",
        },
        "binaryInput": {
            "encoding": "ADVX-BIN/1",
            "header": {
                "$ref": "#/components/schemas/BinaryEnvelopeHeader",
            },
        },
    }
