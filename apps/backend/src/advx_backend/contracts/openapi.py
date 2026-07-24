from typing import Any

from advx_backend.contracts.binary import BinaryEnvelopeHeader
from advx_backend.contracts.debug import (
    ObservationWaveTrace,
    TraceQuery,
    TraceQueryResponse,
    ViewerRequestTrace,
)
from advx_backend.contracts.protocol import (
    AUDIENCE_CONTRACT_VERSION,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_HEADER,
    REPLAY_SCHEMA_VERSION,
    TRACE_SCHEMA_VERSION,
)
from advx_backend.contracts.realtime import ClientMessageEnvelope, ServerMessageEnvelope
from advx_backend.contracts.replay import ReplayBundle, ReplayRequest, ReplayResult
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    RuntimeApplyRequest,
    RuntimeApplyResponse,
    RuntimeQueryResponse,
    RuntimeRollbackRequest,
    ViewerBarrageEvent,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
)

VERSIONED_SCHEMA_MODELS = (
    CanonicalRuntimeSpec,
    RuntimeApplyRequest,
    RuntimeApplyResponse,
    RuntimeQueryResponse,
    RuntimeRollbackRequest,
    ViewerGenerationRequest,
    ViewerGenerationResponse,
    ViewerBarrageEvent,
    ViewerRequestTrace,
    ObservationWaveTrace,
    TraceQuery,
    TraceQueryResponse,
    ReplayBundle,
    ReplayRequest,
    ReplayResult,
)


def add_realtime_schemas(schema: dict[str, Any]) -> dict[str, Any]:
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    for model in (
        ClientMessageEnvelope,
        ServerMessageEnvelope,
        BinaryEnvelopeHeader,
        *VERSIONED_SCHEMA_MODELS,
    ):
        model_schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}",
        )
        definitions = model_schema.pop("$defs", {})
        for name, definition in definitions.items():
            schemas.setdefault(name, definition)
        schemas[model.__name__] = model_schema

    schema["x-advx-realtime"] = {
        "path": "/ws",
        "protocolVersion": PROTOCOL_VERSION,
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
    schema["x-advx-contracts"] = {
        "protocolVersion": PROTOCOL_VERSION,
        "audienceContractVersion": AUDIENCE_CONTRACT_VERSION,
        "traceSchemaVersion": TRACE_SCHEMA_VERSION,
        "replaySchemaVersion": REPLAY_SCHEMA_VERSION,
        "canonicalRuntimeSpec": {
            "$ref": "#/components/schemas/CanonicalRuntimeSpec",
        },
        "viewerRequest": {
            "$ref": "#/components/schemas/ViewerGenerationRequest",
        },
        "viewerResponse": {
            "$ref": "#/components/schemas/ViewerGenerationResponse",
        },
        "debugTrace": {
            "oneOf": [
                {"$ref": "#/components/schemas/ViewerRequestTrace"},
                {"$ref": "#/components/schemas/ObservationWaveTrace"},
            ],
            "discriminator": {
                "propertyName": "trace_kind",
                "mapping": {
                    "viewer_request": "#/components/schemas/ViewerRequestTrace",
                    "observation_wave": "#/components/schemas/ObservationWaveTrace",
                },
            },
        },
        "replayBundle": {
            "$ref": "#/components/schemas/ReplayBundle",
        },
    }
    _mark_protocol_headers_required(schema)
    return schema


def export_versioned_json_schemas() -> dict[str, Any]:
    return {
        "versions": {
            "protocol": PROTOCOL_VERSION,
            "audienceContract": AUDIENCE_CONTRACT_VERSION,
            "traceSchema": TRACE_SCHEMA_VERSION,
            "replaySchema": REPLAY_SCHEMA_VERSION,
        },
        "schemas": {
            model.__name__: model.model_json_schema()
            for model in VERSIONED_SCHEMA_MODELS
        },
    }


def _mark_protocol_headers_required(schema: dict[str, Any]) -> None:
    for path in schema.get("paths", {}).values():
        for operation in path.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                if (
                    parameter.get("in") == "header"
                    and parameter.get("name") == PROTOCOL_VERSION_HEADER
                ):
                    parameter["required"] = True
                    parameter["schema"] = {
                        "type": "string",
                        "const": str(PROTOCOL_VERSION),
                        "title": "ADVX Protocol Version",
                    }
