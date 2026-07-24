import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from advx_backend.bootstrap import build_runtime
from advx_backend.contracts.openapi import export_versioned_json_schemas
from advx_backend.contracts.protocol import (
    AUDIENCE_CONTRACT_VERSION,
    PROTOCOL_VERSION,
    REPLAY_SCHEMA_VERSION,
    TRACE_SCHEMA_VERSION,
)
from advx_backend.contracts.replay import (
    RecordedProviderOutput,
    ReplayBundle,
    ReplayEvent,
    ReplayMode,
    ReplayRequest,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    EvidenceRef,
    EvidenceSource,
    ProviderRuntimeSpec,
    Room,
    RuntimeApplyRequest,
    ViewerAction,
    ViewerBarrageEvent,
    ViewerGenerationResponse,
)
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.main import create_app


def persona(*, enabled: bool = True) -> PersonaTemplate:
    return PersonaTemplate(
        persona_id="persona-1",
        document_version=1,
        revision=1,
        content_hash="a" * 64,
        display_name="Viewer",
        role="commentator",
        silence_bias=0.2,
        burst_bias=0.4,
        repetition_bias=0.1,
        cooldown_ms=500,
        enabled=enabled,
    )


def mode_definition() -> ModeDefinition:
    return ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1-memes",
        revision=1,
        viewer_count=2,
        persona_ids=["persona-1"],
        persona_weights={"persona-1": 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=1, maximum=2),
    )


def runtime_spec() -> CanonicalRuntimeSpec:
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Default Room",
            created_at_ms=100,
            updated_at_ms=100,
        ),
        active_mode_id="mode-1",
        personas=[persona()],
        modes=[mode_definition()],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="model-1",
            viewer_model="model-1",
            memory_model="model-1",
            visual_summary_model="model-1",
        ),
    )


def test_version_constants_are_locked() -> None:
    assert PROTOCOL_VERSION == 2
    assert AUDIENCE_CONTRACT_VERSION == 1
    assert TRACE_SCHEMA_VERSION == 1
    assert REPLAY_SCHEMA_VERSION == 1


def test_canonical_runtime_spec_is_stable_and_rejects_invalid_references() -> None:
    first = runtime_spec()
    second = CanonicalRuntimeSpec.model_validate(first.model_dump(mode="json"))

    assert first.canonical_json() == second.canonical_json()
    assert first.config_hash() == second.config_hash()

    payload = first.model_dump(mode="json")
    payload["active_mode_id"] = "unknown"
    with pytest.raises(ValidationError, match="active_mode_id"):
        CanonicalRuntimeSpec.model_validate(payload)


def test_canonical_runtime_spec_matches_desktop_numeric_fixture() -> None:
    fixture_path = (
        Path(__file__).parents[3]
        / "tests"
        / "fixtures"
        / "cs2"
        / "canonical_runtime_numeric_parity.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    spec = CanonicalRuntimeSpec.model_validate(fixture["spec"])

    canonical_json = spec.canonical_json()
    canonical_payload = json.loads(canonical_json)
    first_persona = canonical_payload["personas"][0]
    second_persona = canonical_payload["personas"][1]
    weights = canonical_payload["modes"][0]["persona_weights"]

    assert first_persona["silence_bias"] == 0
    assert isinstance(first_persona["silence_bias"], int)
    assert second_persona["silence_bias"] == 1
    assert isinstance(second_persona["silence_bias"], int)
    assert first_persona["repetition_bias"] == 0.125
    assert isinstance(first_persona["repetition_bias"], float)
    assert weights == {"one-bias": 1, "zero-bias": 0}
    assert all(isinstance(weight, int) for weight in weights.values())
    assert (
        '"ordering_probe":{"10":"ten","2":"two",'
        '"\U0001f600":"astral","\ue000":"bmp-private-use"}'
    ) in canonical_json
    assert spec.config_hash() == fixture["expected_config_hash"]

    # Canonicalization changes only the byte representation, not the Pydantic model.
    assert spec.personas[0].silence_bias == 0.0
    assert isinstance(spec.personas[0].silence_bias, float)
    assert spec.personas[0].repetition_bias == 0.125
    assert isinstance(spec.personas[0].repetition_bias, float)


def test_runtime_apply_rejects_contract_and_hash_mismatches() -> None:
    spec = runtime_spec()

    with pytest.raises(ValidationError, match="audience_contract_version"):
        RuntimeApplyRequest(
            apply_id="apply-1",
            base_revision=0,
            audience_contract_version=2,
            canonical_runtime_spec=spec,
            client_config_hash=spec.config_hash(),
        )

    with pytest.raises(ValidationError, match="client_config_hash"):
        RuntimeApplyRequest(
            apply_id="apply-1",
            base_revision=0,
            canonical_runtime_spec=spec,
            client_config_hash="0" * 64,
        )


def test_mode_and_viewer_response_enforce_runtime_boundaries() -> None:
    mode_payload = mode_definition().model_dump(mode="json")
    mode_payload["normal_response_range"] = {"minimum": 0, "maximum": 3}
    with pytest.raises(ValidationError, match="viewer_count"):
        ModeDefinition.model_validate(mode_payload)

    with pytest.raises(ValidationError, match="silence cannot include text"):
        ViewerGenerationResponse(
            generation_request_id="request-1",
            viewer_instance_id="viewer-1",
            viewer_sequence=1,
            action=ViewerAction.SILENCE,
            text="not allowed",
            reaction_type="comment",
        )

    event_evidence = EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1")
    assert event_evidence.frame_index is None
    with pytest.raises(ValidationError, match="event evidence"):
        EvidenceRef(source=EvidenceSource.EVENT, event_id="event-1", frame_index=0)


def test_barrage_event_requires_viewer_identity() -> None:
    payload = {
        "barrage_id": "barrage-1",
        "room_id": "room-1",
        "session_id": "session-1",
        "audience_epoch": 1,
        "observation_id": "observation-1",
        "generation_request_id": "request-1",
        "viewer_instance_id": "viewer-1",
        "persona_id": "persona-1",
        "display_name": "Viewer",
        "viewer_sequence": 1,
        "reaction_type": "comment",
        "evidence_refs": [],
        "text": "hello",
        "created_at_ms": 100,
        "expires_at_ms": 200,
    }
    assert ViewerBarrageEvent.model_validate(payload).viewer_instance_id == "viewer-1"

    del payload["viewer_instance_id"]
    with pytest.raises(ValidationError, match="viewer_instance_id"):
        ViewerBarrageEvent.model_validate(payload)


def test_replay_is_deterministic_by_default_and_live_requires_opt_in() -> None:
    spec = runtime_spec()
    bundle = ReplayBundle(
        bundle_id="bundle-1",
        created_at_ms=100,
        seed=42,
        virtual_clock_start_ms=100,
        config_hash=spec.config_hash(),
        canonical_runtime_spec=spec,
        events=[
            ReplayEvent(
                sequence=1,
                event_type="viewer.completed",
                occurred_at_ms=110,
                payload={"generation_request_id": "request-1"},
            )
        ],
        recorded_provider_outputs=[
            RecordedProviderOutput(
                generation_request_id="request-1",
                provider_role="viewer",
                output={"action": "silence"},
            )
        ],
    )

    request = ReplayRequest(bundle=bundle)
    assert request.mode is ReplayMode.RECORDED
    assert request.allow_external_provider_calls is False

    with pytest.raises(ValidationError, match="explicit external Provider opt-in"):
        ReplayRequest(mode=ReplayMode.LIVE, bundle=bundle)


def test_openapi_and_standalone_exports_include_versioned_contracts(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token="test-local-token", data_directory=tmp_path))
    schemas = export_versioned_json_schemas()

    assert schemas["versions"] == {
        "protocol": 2,
        "audienceContract": 1,
        "traceSchema": 1,
        "replaySchema": 1,
    }
    assert "CanonicalRuntimeSpec" in schemas["schemas"]
    assert "ReplayBundle" in schemas["schemas"]

    extension = app.openapi()["x-advx-contracts"]
    assert extension["protocolVersion"] == 2
    assert extension["canonicalRuntimeSpec"] == {
        "$ref": "#/components/schemas/CanonicalRuntimeSpec"
    }


def test_websocket_explicitly_rejects_v1_handshake(tmp_path: Path) -> None:
    app = create_app(runtime=build_runtime(local_token="test-local-token", data_directory=tmp_path))

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(
                {
                    "type": "client.hello",
                    "protocol_version": 1,
                    "token": "test-local-token",
                }
            )
            error = websocket.receive_json()
            assert error["code"] == "version_mismatch"
            assert error["supported_version"] == 2
            assert error["protocol_version"] == 2
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()

    assert disconnect.value.code == 4406
