from typing import Any

from advx_backend.contracts.binary import BinaryEnvelopeHeader
from advx_backend.contracts.generation import (
    CrowdDecision,
    DirectorRequest,
    MemeCandidate,
    ViewerGenerationRequest,
    ViewerGenerationResult,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from advx_backend.contracts.realtime import ClientMessageEnvelope, ServerMessageEnvelope


def add_realtime_schemas(schema: dict[str, Any]) -> dict[str, Any]:
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    for model in (
        ClientMessageEnvelope,
        ServerMessageEnvelope,
        BinaryEnvelopeHeader,
        DirectorRequest,
        CrowdDecision,
        MemeCandidate,
        ViewerGenerationRequest,
        ViewerGenerationResult,
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
    _mark_protocol_headers_required(schema)
    return schema


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
