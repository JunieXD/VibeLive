import json
from pathlib import Path

from advx_backend.contracts.debug import (
    AiCallQuery,
    AiCallRole,
    AiCallStatus,
    AiCallTimelineEvent,
    AiCallTrace,
)
from advx_backend.infrastructure.logging.ai_call_store import AiCallStore


def _completed_call(call_id: str, *, started_at_ms: int) -> AiCallTrace:
    return AiCallTrace(
        call_id=call_id,
        correlation_id=f"correlation-{call_id}",
        role=AiCallRole.VIEWER,
        status=AiCallStatus.SUCCEEDED,
        provider="openai-compatible",
        model_id="model-1",
        endpoint="/v1/chat/completions",
        session_id="session-1",
        started_at_ms=started_at_ms,
        updated_at_ms=started_at_ms + 1,
        completed_at_ms=started_at_ms + 1,
        duration_ms=1,
        timeline=[
            AiCallTimelineEvent(
                stage=AiCallStatus.SUCCEEDED,
                at_ms=started_at_ms + 1,
            )
        ],
    )


def test_capacity_eviction_appends_without_rewriting_full_log(tmp_path: Path) -> None:
    path = tmp_path / "ai-calls.jsonl"
    store = AiCallStore(max_items=2, path=path)
    store.upsert(_completed_call("call-1", started_at_ms=1))
    store.upsert(_completed_call("call-2", started_at_ms=2))

    store.upsert(_completed_call("call-3", started_at_ms=3))

    persisted = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [item["call_id"] for item in persisted] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    assert [item.call_id for item in store.query(AiCallQuery(limit=10)).items] == [
        "call-3",
        "call-2",
    ]

    reloaded = AiCallStore(max_items=2, path=path)
    assert [item.call_id for item in reloaded.query(AiCallQuery(limit=10)).items] == [
        "call-3",
        "call-2",
    ]
