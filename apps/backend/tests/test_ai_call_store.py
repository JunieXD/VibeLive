import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from advx_backend.api.http.debug import create_debug_router
from advx_backend.application.debug_service import DebugService
from advx_backend.contracts.debug import (
    AiCallQuery,
    AiCallRequestSummary,
    AiCallRole,
    AiCallStatus,
    AiCallTimelineEvent,
    AiCallTrace,
    MemoryReferenceTrace,
    ObservationWaveStatus,
    ObservationWaveTrace,
)
from advx_backend.contracts.protocol import PROTOCOL_VERSION_HEADER
from advx_backend.domain.observation_wave import MAX_FRAME_BUNDLE_SIZE
from advx_backend.infrastructure.logging.ai_call_store import AiCallStore
from advx_backend.infrastructure.logging.trace_store import (
    TraceStore,
    UnsafeTraceArtifactError,
)

LOCAL_TOKEN = "test-local-token"


def ai_call(
    call_id: str,
    *,
    correlation_id: str = "correlation-1",
    session_id: str = "session-1",
    role: AiCallRole = AiCallRole.VIEWER,
    status: AiCallStatus = AiCallStatus.PREPARING,
    started_at_ms: int = 10,
    updated_at_ms: int = 20,
    request: AiCallRequestSummary | None = None,
) -> AiCallTrace:
    return AiCallTrace(
        call_id=call_id,
        correlation_id=correlation_id,
        role=role,
        status=status,
        provider="openai-compatible",
        model_id="model-1",
        endpoint="/v1/chat/completions",
        room_id="room-1",
        session_id=session_id,
        audience_epoch=1,
        observation_id="observation-1",
        generation_request_id="generation-1",
        viewer_instance_id="viewer-1",
        started_at_ms=started_at_ms,
        updated_at_ms=updated_at_ms,
        request=request,
        timeline=[
            AiCallTimelineEvent(stage=status, at_ms=updated_at_ms),
        ],
    )


def test_upsert_is_bounded_and_same_call_does_not_consume_capacity() -> None:
    store = AiCallStore(max_items=2)
    store.upsert(ai_call("call-1"))
    store.upsert(
        ai_call(
            "call-1",
            status=AiCallStatus.SENT,
            updated_at_ms=30,
        )
    )
    store.upsert(ai_call("call-2"))

    first_page = store.query(AiCallQuery(limit=1))
    second_page = store.query(AiCallQuery(limit=1, cursor=first_page.next_cursor))

    assert first_page.metadata["retained"] == 2
    assert first_page.items[0].call_id == "call-2"
    assert second_page.items[0].call_id == "call-1"
    assert second_page.items[0].status is AiCallStatus.SENT
    assert second_page.next_cursor is None

    store.upsert(ai_call("call-3"))
    assert [item.call_id for item in store.query().items] == ["call-3", "call-2"]


def test_query_filters_by_session_role_status_and_correlation() -> None:
    store = AiCallStore()
    store.upsert(
        ai_call(
            "call-viewer",
            correlation_id="matching",
            status=AiCallStatus.SUCCEEDED,
        )
    )
    store.upsert(
        ai_call(
            "call-legacy-director",
            correlation_id="other",
            role=AiCallRole.LEGACY_DIRECTOR,
            status=AiCallStatus.FAILED,
        )
    )
    store.upsert(ai_call("call-other-session", session_id="session-2"))

    result = store.query(
        AiCallQuery(
            session_id="session-1",
            role=AiCallRole.VIEWER,
            status=AiCallStatus.SUCCEEDED,
            correlation_id="matching",
        )
    )

    assert [item.call_id for item in result.items] == ["call-viewer"]
    assert result.metadata["matched"] == 1


def test_keyset_cursor_does_not_shift_when_new_calls_arrive() -> None:
    store = AiCallStore()
    store.upsert(ai_call("call-1", started_at_ms=10))
    store.upsert(ai_call("call-2", started_at_ms=20))
    store.upsert(ai_call("call-3", started_at_ms=30))

    first_page = store.query(AiCallQuery(limit=2))
    assert [item.call_id for item in first_page.items] == ["call-3", "call-2"]
    assert first_page.next_cursor is not None

    store.upsert(ai_call("call-4", started_at_ms=40))
    second_page = store.query(
        AiCallQuery(limit=2, cursor=first_page.next_cursor)
    )

    assert [item.call_id for item in second_page.items] == ["call-1"]
    assert second_page.next_cursor is None


def test_concurrent_upsert_and_query_keep_store_consistent(tmp_path: Path) -> None:
    store = AiCallStore(max_items=200, path=tmp_path / "ai-calls.jsonl")

    def write(index: int) -> None:
        store.upsert(
            ai_call(
                f"call-{index:03d}",
                started_at_ms=index,
                updated_at_ms=index + 1,
            )
        )
        store.query(AiCallQuery(limit=10))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(1, 101)))

    result = store.query(AiCallQuery(limit=200))
    assert len(result.items) == 100
    assert len({item.call_id for item in result.items}) == 100


def test_jsonl_reload_marks_unfinished_calls_interrupted(tmp_path: Path) -> None:
    path = tmp_path / "ai-calls.jsonl"
    first = AiCallStore(path=path, clock_ms=lambda: 50)
    first.upsert(ai_call("call-pending", status=AiCallStatus.STREAMING))
    first.upsert(ai_call("call-done", status=AiCallStatus.SUCCEEDED))

    reloaded = AiCallStore(path=path, clock_ms=lambda: 100)
    calls = {item.call_id: item for item in reloaded.query().items}

    assert calls["call-pending"].status is AiCallStatus.INTERRUPTED
    assert calls["call-pending"].completed_at_ms == 100
    assert calls["call-pending"].duration_ms == 90
    assert calls["call-pending"].timeline[-1].detail == {
        "reason": "backend_restart"
    }
    assert calls["call-done"].status is AiCallStatus.SUCCEEDED

    loaded_again = AiCallStore(path=path, clock_ms=lambda: 200)
    assert loaded_again.query(
        AiCallQuery(status=AiCallStatus.INTERRUPTED)
    ).items[0].updated_at_ms == 100


def test_jsonl_reload_migrates_legacy_director_role(tmp_path: Path) -> None:
    path = tmp_path / "ai-calls.jsonl"
    legacy = ai_call("call-legacy").model_dump(mode="json")
    legacy["role"] = "director"
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    reloaded = AiCallStore(path=path)

    assert reloaded.query().items[0].role is AiCallRole.LEGACY_DIRECTOR
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["role"] == "legacy_director"


def test_trace_store_migrates_legacy_trace_fields(tmp_path: Path) -> None:
    path = tmp_path / "viewer-traces.jsonl"
    trace = ObservationWaveTrace(
        trace_id="wave-1",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        config_hash="0" * 64,
        observation_id="observation-1",
        created_at_ms=10,
        deadline_at_ms=20,
        triggers=["user_text"],
        memory=MemoryReferenceTrace(
            room_id="room-1",
            memory_revision=0,
        ),
        status=ObservationWaveStatus.COMPLETED,
    ).model_dump(mode="json")
    trace["director_status"] = trace.pop("status")
    trace["decision_source"] = "director"
    viewer_trace = {
        "trace_kind": "viewer_request",
        "trace_schema_version": 1,
        "trace_id": "request-1",
        "room_id": "room-1",
        "session_id": "session-1",
        "audience_epoch": 1,
        "config_hash": "0" * 64,
        "observation_id": "observation-1",
        "director_budget": {"minimum": 0, "maximum": 1},
        "director_decision": {
            "decision_id": "decision-1",
            "room_id": "room-1",
            "session_id": "session-1",
            "audience_epoch": 1,
            "observation_id": "observation-1",
            "created_at_ms": 10,
            "expires_at_ms": 20,
            "decision_source": "director",
            "evidence_frame_indexes": list(range(MAX_FRAME_BUNDLE_SIZE + 3)),
        },
        "viewer_instance_id": "viewer-1",
        "viewer_sequence": 1,
        "persona_revision": 1,
        "instance_variant": {
            "expression_length": 0.5,
            "skepticism": 0.5,
            "encouragement": 0.5,
            "meme_affinity": 0.5,
            "focus": "gameplay",
            "silence_tendency": 0.5,
        },
        "memory": {"room_id": "room-1", "memory_revision": 0},
        "prompt_manifest": {
            "template_id": "viewer-generation-v1",
            "template_revision": 1,
            "input_hash": "0" * 64,
        },
        "provider": {"provider_role": "viewer", "model_id": "model-1", "queued_at_ms": 10},
        "response_status": "completed",
        "validation": {"accepted": True},
    }
    path.write_text(
        "\n".join([json.dumps(trace), json.dumps(viewer_trace)]) + "\n",
        encoding="utf-8",
    )

    reloaded = TraceStore(path=path)

    assert reloaded.query().waves[0].status is ObservationWaveStatus.COMPLETED
    persisted = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert persisted[0]["status"] == "completed"
    assert persisted[0]["decision_source"] == "legacy_director"
    assert "director_status" not in persisted[0]
    assert persisted[1]["decision"]["decision_id"] == "decision-1"
    assert persisted[1]["decision"]["decision_source"] == "legacy_director"
    assert persisted[1]["decision"]["evidence_frame_indexes"] == list(
        range(MAX_FRAME_BUNDLE_SIZE)
    )
    assert "director_budget" not in persisted[1]
    assert "director_decision" not in persisted[1]


def test_upsert_rejects_unredacted_preview() -> None:
    store = AiCallStore()
    trace = ai_call(
        "unsafe",
        request=AiCallRequestSummary(
            input_preview={"prompt": "raw provider prompt"},
        ),
    )

    with pytest.raises(UnsafeTraceArtifactError):
        store.upsert(trace)

    assert store.query().items == []


@pytest.mark.parametrize("field", ["model_api_key", "openaiAccessToken"])
def test_upsert_rejects_namespaced_or_camel_case_credentials(field: str) -> None:
    store = AiCallStore()
    trace = ai_call(
        "unsafe-credential",
        request=AiCallRequestSummary(
            input_preview={field: "must-not-persist"},
        ),
    )

    with pytest.raises(UnsafeTraceArtifactError):
        store.upsert(trace)


def test_upsert_rejects_namespaced_credentials_in_free_text() -> None:
    store = AiCallStore()
    trace = ai_call(
        "unsafe-inline-credential",
        request=AiCallRequestSummary(
            input_preview={
                "public_text": (
                    "model_api_key=namespaced-secret "
                    "openaiAccessToken=camel-secret"
                )
            },
        ),
    )

    with pytest.raises(UnsafeTraceArtifactError):
        store.upsert(trace)


def test_debug_ai_calls_endpoint_exposes_filtered_traces() -> None:
    ai_store = AiCallStore()
    ai_store.upsert(ai_call("call-1", status=AiCallStatus.SUCCEEDED))
    service = DebugService(TraceStore(), ai_call_store=ai_store)
    app = FastAPI()
    app.state.debug_service = service
    app.include_router(create_debug_router(local_token=LOCAL_TOKEN))
    client = TestClient(app)

    response = client.get(
        "/debug/ai-calls",
        params={
            "session_id": "session-1",
            "role": "viewer",
            "status": "succeeded",
            "correlation_id": "correlation-1",
        },
        headers={
            "Authorization": f"Bearer {LOCAL_TOKEN}",
            PROTOCOL_VERSION_HEADER: "3",
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["call_id"] == "call-1"


def test_debug_ai_call_image_endpoint_exposes_only_ephemeral_previews() -> None:
    service = DebugService(TraceStore())
    preview_id = service.capture_ai_call_image("data:image/png;base64,cGl4ZWw=")
    assert preview_id is not None
    app = FastAPI()
    app.state.debug_service = service
    app.include_router(create_debug_router(local_token=LOCAL_TOKEN))
    client = TestClient(app)

    response = client.get(
        f"/debug/ai-calls/images/{preview_id}",
        headers={
            "Authorization": f"Bearer {LOCAL_TOKEN}",
            PROTOCOL_VERSION_HEADER: "3",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "mime_type": "image/png",
        "data_url": "data:image/png;base64,cGl4ZWw=",
    }
    assert service.query_ai_calls().items == []
