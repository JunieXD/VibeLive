import json
from pathlib import Path

from advx_backend.contracts.debug import (
    MemoryReferenceTrace,
    ObservationWaveStatus,
    ObservationWaveTrace,
)
from advx_backend.domain.observation_wave import ObservationTrigger
from advx_backend.infrastructure.logging.trace_store import TraceStore


def _wave(index: int) -> ObservationWaveTrace:
    return ObservationWaveTrace(
        trace_id=f"wave-{index}",
        room_id="room-1",
        session_id="session-1",
        audience_epoch=1,
        config_hash="0" * 64,
        observation_id=f"observation-{index}",
        created_at_ms=index,
        deadline_at_ms=index + 10_000,
        triggers=[ObservationTrigger.USER_TEXT],
        event_ids=[f"event-{index}"],
        trigger_event_ids=[f"event-{index}"],
        memory=MemoryReferenceTrace(room_id="room-1", memory_revision=0),
        status=ObservationWaveStatus.COMPLETED,
    )


def _persisted_trace_ids(path: Path) -> list[str]:
    return [
        json.loads(line)["trace_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_capacity_eviction_appends_without_rewriting_full_log(tmp_path: Path) -> None:
    path = tmp_path / "viewer-traces.jsonl"
    store = TraceStore(max_items=2, path=path)

    store.append(_wave(1))
    store.append(_wave(2))
    store.append(_wave(3))

    assert _persisted_trace_ids(path) == ["wave-1", "wave-2", "wave-3"]
    reloaded = TraceStore(max_items=2, path=path)
    assert [item.trace_id for item in reloaded.query().waves] == ["wave-2", "wave-3"]


def test_append_log_is_periodically_compacted(tmp_path: Path) -> None:
    path = tmp_path / "viewer-traces.jsonl"
    store = TraceStore(max_items=2, path=path)

    for index in range(9):
        store.append(_wave(index))

    assert _persisted_trace_ids(path) == ["wave-7", "wave-8"]


def test_truncated_tail_is_removed_before_appending(tmp_path: Path) -> None:
    path = tmp_path / "viewer-traces.jsonl"
    path.write_text(
        json.dumps(_wave(1).model_dump(mode="json")) + "\n" + '{"trace_kind":',
        encoding="utf-8",
    )

    store = TraceStore(max_items=2, path=path)
    store.append(_wave(2))

    assert _persisted_trace_ids(path) == ["wave-1", "wave-2"]
