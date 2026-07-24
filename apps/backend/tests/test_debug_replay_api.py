from fastapi import FastAPI
from fastapi.testclient import TestClient

from advx_backend.application.replay_service import ReplayService
from advx_backend.contracts.debug import (
    DebugRuntimeSnapshot,
    DirectorBudgetTrace,
    PromptManifest,
    ProviderTrace,
    TraceQuery,
    TraceQueryResponse,
    TraceResponseStatus,
    ValidationTrace,
    ViewerRequestTrace,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.contracts.replay import (
    RecordedProviderOutput,
    ReplayBundle,
    ReplayEvent,
    ReplayRequest,
)
from advx_backend.contracts.viewer_runtime import (
    CanonicalRuntimeSpec,
    ProviderRuntimeSpec,
    Room,
)
from advx_backend.domain.crowd_decision import CrowdDecision
from advx_backend.domain.memory import RoomMemorySlice
from advx_backend.domain.persona import ModeDefinition, PersonaTemplate, ResponseRange
from advx_backend.domain.viewer import ViewerInstanceVariant

LOCAL_TOKEN = "test-local-token"


def headers(*, version: str = "2") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {LOCAL_TOKEN}",
        PROTOCOL_VERSION_HEADER: version,
    }


def runtime_spec() -> CanonicalRuntimeSpec:
    persona = PersonaTemplate(
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
    )
    mode = ModeDefinition(
        mode_id="mode-1",
        namespace_id="mode-1",
        revision=1,
        viewer_count=1,
        persona_ids=["persona-1"],
        persona_weights={"persona-1": 1},
        normal_response_range=ResponseRange(minimum=0, maximum=1),
        highlight_response_range=ResponseRange(minimum=1, maximum=1),
    )
    return CanonicalRuntimeSpec(
        config_revision=1,
        room=Room(
            room_id="room-1",
            display_name="Room",
            created_at_ms=100,
            updated_at_ms=100,
        ),
        active_mode_id="mode-1",
        personas=[persona],
        modes=[mode],
        provider=ProviderRuntimeSpec(
            provider_profile_id="provider-1",
            director_model="director",
            viewer_model="viewer",
            memory_model="memory",
            visual_summary_model="vision",
        ),
    )


def trace() -> ViewerRequestTrace:
    return ViewerRequestTrace(
        trace_id="trace-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        config_hash="b" * 64,
        observation_id="observation-1",
        director_budget=DirectorBudgetTrace(
            minimum=0,
            maximum=1,
            available_viewer_ids=["viewer-1"],
        ),
        director_decision=CrowdDecision(
            decision_id="decision-1",
            room_id="room-1",
            session_id="session-1",
            audience_epoch=1,
            observation_id="observation-1",
            selected_viewer_ids=["viewer-1"],
            created_at_ms=100,
            expires_at_ms=200,
        ),
        viewer_instance_id="viewer-1",
        viewer_sequence=1,
        persona_revision=1,
        instance_variant=ViewerInstanceVariant(
            expression_length=0.5,
            skepticism=0.5,
            encouragement=0.5,
            meme_affinity=0.5,
            focus="gameplay",
            silence_tendency=0.2,
        ),
        memory=RoomMemorySlice(room_id="room-1", memory_revision=0),
        prompt_manifest=PromptManifest(
            template_id="viewer-v1",
            template_revision=1,
            input_hash="c" * 64,
            sections=["public-context"],
        ),
        provider=ProviderTrace(
            provider_role="viewer",
            model_id="model-1",
            queued_at_ms=100,
            completed_at_ms=110,
        ),
        response_status=TraceResponseStatus.COMPLETED,
        validation=ValidationTrace(accepted=True),
    )


def bundle() -> ReplayBundle:
    spec = runtime_spec()
    roles = ("director", "viewer", "memory", "visual_summary", "asr")
    counts = {"director": 3, "viewer": 1, "memory": 1}
    outputs = {
        "director": {"reason_codes": ["recorded"]},
        "viewer": {"action": "silence"},
        "memory": {
            "candidates": [
                {
                    "memory_type": "shared_experience",
                    "content": "recorded replay memory",
                    "tags": ["recorded"],
                    "importance": 0.5,
                    "confidence": 1,
                }
            ]
        },
        "visual_summary": {"summary": "recorded replay frame"},
        "asr": {
            "text": "recorded replay transcript",
            "final": True,
            "started_at_ms": 1_010,
            "ended_at_ms": 1_020,
        },
    }
    return ReplayBundle(
        bundle_id="bundle-1",
        created_at_ms=100,
        seed=42,
        virtual_clock_start_ms=1_000,
        config_hash=spec.config_hash(),
        canonical_runtime_spec=spec,
        events=[
            ReplayEvent(
                sequence=index,
                event_type=f"{role}.completed",
                occurred_at_ms=1_100 + index,
                payload={
                    "generation_request_ids": [
                        f"request-{role}-{call_index}"
                        for call_index in range(1, counts.get(role, 1) + 1)
                    ]
                },
            )
            for index, role in enumerate(roles, start=1)
        ],
        recorded_provider_outputs=[
            RecordedProviderOutput(
                generation_request_id=f"request-{role}-{call_index}",
                provider_role=role,
                output=outputs[role],
            )
            for role in roles
            for call_index in range(1, counts.get(role, 1) + 1)
        ],
    )


class RecordingDebugService:
    def __init__(self) -> None:
        self.queries: list[TraceQuery] = []
        self.exports: list[TraceQuery] = []

    def query(self, query: TraceQuery) -> TraceQueryResponse:
        self.queries.append(query)
        return TraceQueryResponse(
            items=[trace()],
            next_cursor="cursor-2",
            metadata={"bounded": True},
        )

    def export_artifact(self, query: TraceQuery) -> dict[str, object]:
        self.exports.append(query)
        return {
            "trace_schema_version": 1,
            "redacted": True,
            "items": [trace().model_dump(mode="json")],
        }

    async def runtime_snapshot(self, session_id: str) -> DebugRuntimeSnapshot:
        return DebugRuntimeSnapshot(
            session_id=session_id,
            room_id="room-1",
            audience_epoch=1,
            accepting_results=True,
            config=runtime_spec(),
            pool={
                "room_id": "room-1",
                "session_id": session_id,
                "audience_epoch": 1,
                "mode_id": "mode-1",
                "session_seed": "redacted",
                "viewers": [],
            },
            waves=[],
            director_budgets=[],
            queue={"depth": 3, "capacity": 12},
            telemetry={
                "selected": 4,
                "queued": 4,
                "dispatched": 3,
                "completed": 2,
                "silence": 1,
                "published": 1,
                "rejected": 0,
                "expired": 0,
            },
            context_refs={
                "event_ids": ["event-1"],
                "frame_hashes": ["a" * 64],
                "memory_ids": ["memory-1"],
            },
            memory={"revision": 2, "ids": ["memory-1"]},
            memes={"ids": ["meme-1"], "candidate_ids": ["candidate-1"]},
            history=[{"trace_id": "trace-1", "response_status": "completed"}],
        )


class FailingIfCalledProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def replay(self, replay_bundle: ReplayBundle) -> None:
        del replay_bundle
        self.calls += 1


def app_with(
    *,
    debug_service: object | None = None,
    replay_service: object | None = None,
) -> FastAPI:
    from advx_backend.api.http.debug import create_debug_router

    app = FastAPI()
    if debug_service is not None:
        app.state.debug_service = debug_service
    if replay_service is not None:
        app.state.replay_service = replay_service
    app.include_router(create_debug_router(local_token=LOCAL_TOKEN))
    return app


def test_trace_query_maps_all_filters_and_cursor_to_trace_query() -> None:
    service = RecordingDebugService()

    with TestClient(app_with(debug_service=service)) as client:
        response = client.get(
            "/debug/traces",
            headers=headers(),
            params={
                "room_id": "room-1",
                "session_id": "session-1",
                "observation_id": "observation-1",
                "viewer_instance_id": "viewer-1",
                "response_status": "completed",
                "cursor": "cursor-1",
                "limit": 25,
            },
        )

    assert response.status_code == 200
    assert response.json()["next_cursor"] == "cursor-2"
    assert service.queries == [
        TraceQuery(
            room_id="room-1",
            session_id="session-1",
            observation_id="observation-1",
            viewer_instance_id="viewer-1",
            response_status=TraceResponseStatus.COMPLETED,
            cursor="cursor-1",
            limit=25,
        )
    ]


def test_trace_export_returns_a_strictly_redacted_artifact() -> None:
    service = RecordingDebugService()

    with TestClient(app_with(debug_service=service)) as client:
        response = client.post(
            "/debug/traces/export",
            headers=headers(),
            json={"session_id": "session-1", "limit": 100},
        )

    assert response.status_code == 200
    artifact = response.json()
    serialized = response.text.lower()
    assert artifact["redacted"] is True
    assert artifact["trace_schema_version"] == 1
    for forbidden in ("api_key", "authorization", "prompt", "raw_response", "base64"):
        assert forbidden not in serialized
    assert service.exports == [TraceQuery(session_id="session-1", limit=100)]


def test_runtime_snapshot_returns_one_redacted_debug_view() -> None:
    service = RecordingDebugService()

    with TestClient(app_with(debug_service=service)) as client:
        response = client.get(
            "/debug/runtime/session-1",
            headers=headers(),
        )

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["protocol_version"] == 2
    assert snapshot["redacted"] is True
    assert snapshot["session_id"] == "session-1"
    assert snapshot["config"]["config_revision"] == 1
    assert snapshot["pool"]["mode_id"] == "mode-1"
    assert snapshot["queue"] == {"depth": 3, "capacity": 12}
    assert snapshot["telemetry"]["completed"] == 2
    assert snapshot["context_refs"]["memory_ids"] == ["memory-1"]
    serialized = response.text.lower()
    for forbidden in ("api_key", "authorization", "raw_response", "base64"):
        assert forbidden not in serialized


def test_recorded_replay_never_calls_external_provider_through_http() -> None:
    provider = FailingIfCalledProvider()

    with TestClient(
        app_with(replay_service=ReplayService(live_provider=provider))
    ) as client:
        response = client.post(
            "/debug/replay",
            headers=headers(),
            json=ReplayRequest(bundle=bundle()).model_dump(mode="json"),
        )

    assert response.status_code == 200
    assert response.json()["deterministic_proof"] is True
    assert response.json()["credentialed_provider_proof"] is False
    assert provider.calls == 0


def test_live_replay_without_explicit_provider_opt_in_is_rejected_by_contract() -> None:
    provider = FailingIfCalledProvider()
    payload = ReplayRequest(bundle=bundle()).model_dump(mode="json")
    payload["mode"] = "live"

    with TestClient(
        app_with(replay_service=ReplayService(live_provider=provider))
    ) as client:
        response = client.post("/debug/replay", headers=headers(), json=payload)

    assert response.status_code == 422
    assert provider.calls == 0


def test_missing_debug_service_is_reported_as_unavailable() -> None:
    with TestClient(app_with(replay_service=ReplayService())) as client:
        response = client.get("/debug/traces", headers=headers())

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "debug_service_unavailable"


def test_missing_replay_service_is_reported_as_unavailable() -> None:
    with TestClient(app_with(debug_service=RecordingDebugService())) as client:
        response = client.post(
            "/debug/replay",
            headers=headers(),
            json=ReplayRequest(bundle=bundle()).model_dump(mode="json"),
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "replay_service_unavailable"


def test_v1_debug_request_is_rejected_as_an_explicit_conflict() -> None:
    with TestClient(app_with(debug_service=RecordingDebugService())) as client:
        response = client.get("/debug/traces", headers=headers(version="1"))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "protocol_version_conflict"
    assert response.headers[PROTOCOL_VERSION_HEADER] == "2"


def test_unknown_debug_protocol_version_is_rejected_as_unprocessable() -> None:
    with TestClient(app_with(debug_service=RecordingDebugService())) as client:
        response = client.get("/debug/traces", headers=headers(version="999"))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_protocol_version"
    assert response.headers[PROTOCOL_VERSION_HEADER] == "2"
