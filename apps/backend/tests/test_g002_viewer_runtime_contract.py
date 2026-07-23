import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from advx_backend.application.generation_policies import GenerationInvocationPlannerConfig
from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.protocol import PROTOCOL_VERSION
from advx_backend.main import create_app

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "g002_audience_contract_v1.json"
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "advx_backend"
    / "infrastructure"
    / "persistence"
    / "sqlite"
    / "migrations"
    / "versions"
    / "0002_viewer_runtime.py"
)

FROZEN_MODELS = {
    "advx_backend.contracts.session": {
        "SessionStartRequest": {
            "client_start_request_id",
            "audience_contract_version",
            "config_hash",
            "mode",
            "personas",
        },
        "SessionStartResponse": {
            "session_id",
            "state",
            "snapshot_hash",
            "mode_id",
            "mode_namespace_id",
            "mode_revision",
            "viewer_count",
            "allocations",
        },
    },
    "advx_backend.contracts.generation": {
        "DirectorRequest": {
            "request_id",
            "observation",
            "mode",
            "viewer_pool",
            "recent_room_state",
            "active_memes",
        },
        "CrowdDecision": {
            "decision_id",
            "session_id",
            "observation_id",
            "selected_viewer_instance_ids",
            "intent",
            "event_level",
            "silent",
            "evidence_event_ids",
            "created_at_ms",
            "expires_at_ms",
            "meme_candidates",
        },
        "MemeCandidate": {
            "mode_id",
            "mode_namespace_id",
            "evidence_event_ids",
            "text",
            "tags",
            "created_at_ms",
        },
        "ViewerGenerationRequest": {
            "request_id",
            "session_id",
            "observation_id",
            "decision_id",
            "viewer_instance_id",
            "persona_id",
            "observation",
            "compiled_persona",
            "instance_state",
            "persona_memory_revision",
            "persona_memories",
            "active_memes",
        },
        "ViewerGenerationResult": {
            "request_id",
            "session_id",
            "observation_id",
            "decision_id",
            "viewer_instance_id",
            "persona_id",
            "silent",
            "comments",
        },
    },
    "advx_backend.contracts.realtime": {
        "PersonaMemoryRevisionCommitted": {
            "type",
            "protocol_version",
            "persona_id",
            "collection_revision",
            "committed_at_ms",
        },
        "ModeMemeChanged": {
            "type",
            "protocol_version",
            "mode_id",
            "mode_namespace_id",
            "meme_id",
            "revision",
            "status",
            "changed_at_ms",
        },
    },
}

BARRAGE_FIELDS = {
    "barrage_id",
    "session_id",
    "observation_id",
    "decision_id",
    "request_id",
    "viewer_instance_id",
    "persona_id",
    "persona_revision",
    "display_name",
    "display_color",
    "text",
    "accepted_at_ms",
}


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _session_start_payload() -> dict[str, Any]:
    fixture = _fixture()
    return {
        **fixture["normalized_audience_config"],
        **fixture["session_start"],
    }


def _recompute_config_hash(payload: dict[str, Any]) -> None:
    hash_input = {
        key: value
        for key, value in payload.items()
        if key not in {"client_start_request_id", "config_hash"}
    }
    payload["config_hash"] = hashlib.sha256(_canonical_json(hash_input)).hexdigest()


def _reverse_object_order(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _reverse_object_order(value[key])
            for key in reversed(tuple(value))
        }
    if isinstance(value, list):
        return [_reverse_object_order(item) for item in value]
    return value


def test_canonical_hash_fixture_is_stable_across_object_key_order() -> None:
    fixture = _fixture()
    normalized = fixture["normalized_audience_config"]
    expected_hash = fixture["canonicalization"]["expected_hash"]

    assert hashlib.sha256(_canonical_json(normalized)).hexdigest() == expected_hash
    assert (
        hashlib.sha256(_canonical_json(_reverse_object_order(normalized))).hexdigest()
        == expected_hash
    )


def test_transport_protocol_v2_is_independent_from_audience_contract_v1() -> None:
    protocol = importlib.import_module("advx_backend.contracts.protocol")

    assert PROTOCOL_VERSION == 2
    assert getattr(protocol, "AUDIENCE_CONTRACT_VERSION", None) == 1


def test_frozen_session_start_fixture_is_accepted_by_the_request_model() -> None:
    session_contracts = importlib.import_module("advx_backend.contracts.session")
    request_model = getattr(session_contracts, "SessionStartRequest", None)

    assert request_model is not None, "SessionStartRequest is not implemented"
    request = request_model.model_validate(_session_start_payload())

    assert request.config_hash == _fixture()["canonicalization"]["expected_hash"]


def test_session_start_rejects_a_client_hash_that_does_not_match_normalized_content() -> None:
    session_contracts = importlib.import_module("advx_backend.contracts.session")
    payload = _session_start_payload()
    payload["config_hash"] = "0" * 64

    with pytest.raises(ValidationError, match="config_hash does not match"):
        session_contracts.SessionStartRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("case_name", "mutate"),
    [
        (
            "unsupported audience contract",
            lambda payload: payload.update(audience_contract_version=2),
        ),
        (
            "viewer count below range",
            lambda payload: payload["mode"].update(viewer_count=0),
        ),
        (
            "viewer count above range",
            lambda payload: payload["mode"].update(viewer_count=33),
        ),
        (
            "non-positive weight",
            lambda payload: payload["mode"]["persona_weights"].update(
                {"persona-troll": 0}
            ),
        ),
        (
            "dangling roster reference",
            lambda payload: payload["mode"]["persona_ids"].append("persona-missing"),
        ),
        (
            "duplicate roster entry",
            lambda payload: payload["mode"]["persona_ids"].append("persona-troll"),
        ),
        (
            "unweighted roster entry",
            lambda payload: payload["mode"]["persona_weights"].pop("persona-troll"),
        ),
        (
            "duplicate persona document",
            lambda payload: payload["personas"].append(payload["personas"][0]),
        ),
    ],
)
def test_session_start_rejects_invalid_mode_and_persona_graphs(
    case_name: str,
    mutate: Any,
) -> None:
    session_contracts = importlib.import_module("advx_backend.contracts.session")
    request_model = getattr(session_contracts, "SessionStartRequest", None)
    payload = _session_start_payload()
    mutate(payload)

    assert request_model is not None, "SessionStartRequest is not implemented"
    with pytest.raises(ValidationError):
        request_model.model_validate(payload)


@pytest.mark.parametrize(
    ("module_name", "model_name", "required_fields"),
    [
        (module_name, model_name, required_fields)
        for module_name, models in FROZEN_MODELS.items()
        for model_name, required_fields in models.items()
    ],
)
def test_frozen_contract_models_expose_required_wire_fields(
    module_name: str,
    model_name: str,
    required_fields: set[str],
) -> None:
    module = importlib.import_module(module_name)
    model = getattr(module, model_name, None)

    assert model is not None, f"{module_name}.{model_name} is not implemented"
    assert required_fields <= set(model.model_fields)


def test_session_start_openapi_requires_the_frozen_request_and_response() -> None:
    schema = create_app(runtime=build_runtime(local_token="test-local-token")).openapi()
    operation = schema["paths"]["/sessions"]["post"]

    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SessionStartRequest"
    }
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SessionStartResponse"
    }


def test_http_protocol_mismatch_rejects_before_session_start() -> None:
    from fastapi.testclient import TestClient

    app = create_app(runtime=build_runtime(local_token="test-local-token"))
    headers = {
        "Authorization": "Bearer test-local-token",
        "X-ADVX-Protocol-Version": "1",
    }

    with TestClient(app) as client:
        response = client.post("/sessions", headers=headers, json=_session_start_payload())

    assert response.status_code == 426
    assert response.json()["detail"] == {
        "code": "protocol_version_mismatch",
        "message": "The requested protocol version is not supported.",
        "supported_version": 2,
    }


def test_client_start_request_id_replay_returns_the_original_session() -> None:
    from fastapi.testclient import TestClient

    app = create_app(runtime=build_runtime(local_token="test-local-token"))
    headers = {
        "Authorization": "Bearer test-local-token",
        "X-ADVX-Protocol-Version": str(PROTOCOL_VERSION),
    }
    payload = _session_start_payload()

    with TestClient(app) as client:
        created = client.post("/sessions", headers=headers, json=payload)
        replayed = client.post("/sessions", headers=headers, json=payload)

    assert created.status_code == 201
    assert replayed.status_code in {200, 201}
    assert replayed.json() == created.json()


def test_client_start_request_id_reuse_with_another_hash_conflicts() -> None:
    from fastapi.testclient import TestClient

    app = create_app(runtime=build_runtime(local_token="test-local-token"))
    headers = {
        "Authorization": "Bearer test-local-token",
        "X-ADVX-Protocol-Version": str(PROTOCOL_VERSION),
    }
    first_payload = _session_start_payload()
    conflicting_payload = _session_start_payload()
    conflicting_payload["mode"]["viewer_count"] = 4
    _recompute_config_hash(conflicting_payload)

    with TestClient(app) as client:
        created = client.post("/sessions", headers=headers, json=first_payload)
        conflicted = client.post("/sessions", headers=headers, json=conflicting_payload)

    assert created.status_code == 201
    assert conflicted.status_code == 409
    assert conflicted.json()["detail"]["code"] == "client_start_request_conflict"


def test_barrage_realtime_schema_has_viewer_and_immutable_presentation_identity() -> None:
    schema = create_app(runtime=build_runtime(local_token="test-local-token")).openapi()
    barrage = schema["components"]["schemas"]["BarrageSnapshot"]

    assert BARRAGE_FIELDS <= set(barrage["properties"])
    assert BARRAGE_FIELDS <= set(barrage["required"])
    assert "audience_id" not in barrage["properties"]


def test_memory_and_meme_events_are_server_realtime_variants() -> None:
    schema = create_app(runtime=build_runtime(local_token="test-local-token")).openapi()
    server_envelope = json.dumps(schema["components"]["schemas"]["ServerMessageEnvelope"])

    assert "PersonaMemoryRevisionCommitted" in server_envelope
    assert "ModeMemeChanged" in server_envelope


def test_viewer_invocation_batching_is_disabled_by_default() -> None:
    config = GenerationInvocationPlannerConfig()

    assert config.batch_size == 1


def test_viewer_runtime_migration_matches_the_frozen_shape() -> None:
    expected = _fixture()["migration"]

    assert MIGRATION_PATH.exists(), f"missing migration {expected['revision']}"
    migration_source = MIGRATION_PATH.read_text(encoding="utf-8")
    for table_name, columns in expected["required_tables"].items():
        assert table_name in migration_source
        for column_name in columns:
            assert column_name in migration_source
