import base64
import binascii
import json
import os
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from advx_backend.contracts.debug import (
    AiCallListItem,
    AiCallQuery,
    AiCallQueryResponse,
    AiCallRole,
    AiCallStatus,
    AiCallTimelineEvent,
    AiCallTrace,
    ViewerOutputDelivery,
)
from advx_backend.infrastructure.logging.trace_store import assert_redacted_artifact

_TERMINAL_STATUSES = {
    AiCallStatus.SUCCEEDED,
    AiCallStatus.FAILED,
    AiCallStatus.BLOCKED,
    AiCallStatus.CANCELLED,
    AiCallStatus.INTERRUPTED,
}


class AiCallStore:
    """Bounded AI call traces with upsert semantics and optional JSONL persistence."""

    def __init__(
        self,
        *,
        max_items: int = 1_000,
        path: Path | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items must be at least one")
        self._max_items = max_items
        self._path = path
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._items: OrderedDict[str, AiCallTrace] = OrderedDict()
        self._ordered_items: tuple[AiCallTrace, ...] | None = None
        self._writes_since_compaction = 0
        self._lock = RLock()
        if path is not None:
            self._load()

    def upsert(self, trace: AiCallTrace) -> None:
        with self._lock:
            assert_redacted_artifact(trace)
            if trace.call_id not in self._items and len(self._items) >= self._max_items:
                self._items.popitem(last=False)
            self._items[trace.call_id] = trace
            self._ordered_items = None
            if self._path is None:
                return
            # Keep capacity eviction in memory; per-eviction rewrites stall live model work.
            if self._writes_since_compaction >= self._max_items * 4:
                self._persist()
                return
            self._append(trace)

    def record_viewer_output(
        self,
        generation_request_id: str,
        delivery: ViewerOutputDelivery,
        *,
        stage: str,
    ) -> None:
        """Attach output-queue progress to the Viewer call that produced it."""

        with self._lock:
            matches = [
                trace
                for trace in self._items.values()
                if (
                    trace.role is AiCallRole.VIEWER
                    and trace.generation_request_id == generation_request_id
                )
            ]
            if not matches:
                return
            trace = max(matches, key=lambda item: (item.updated_at_ms, item.call_id))
            at_ms = (
                delivery.published_at_ms
                or delivery.scheduled_at_ms
                or delivery.ready_at_ms
            )
            detail = {
                "ready_at_ms": delivery.ready_at_ms,
                "scheduled_at_ms": delivery.scheduled_at_ms,
                "published_at_ms": delivery.published_at_ms,
                "queue_delay_ms": delivery.queue_delay_ms,
                "event_count": delivery.event_count,
                "published_event_count": delivery.published_event_count,
                "interruption_reason": delivery.interruption_reason,
            }
            updated = trace.model_copy(
                update={
                    "updated_at_ms": max(trace.updated_at_ms, at_ms),
                    "viewer_output_delivery": delivery,
                    "timeline": [
                        *trace.timeline[-255:],
                        AiCallTimelineEvent(stage=stage, at_ms=at_ms, detail=detail),
                    ],
                }
            )
            self.upsert(updated)

    def query(self, query: AiCallQuery | None = None) -> AiCallQueryResponse:
        with self._lock:
            query = query or AiCallQuery()
            cursor_key = self._decode_cursor(query.cursor)
            matching = [
                item
                for item in self._ordered_descending()
                if self._matches(item, query)
            ]
            matched_count = len(matching)
            if cursor_key is not None:
                matching = [
                    item for item in matching if self._sort_key(item) < cursor_key
                ]
            page = matching[: query.limit]
            next_cursor = (
                self._encode_cursor(page[-1])
                if page and len(page) < len(matching)
                else None
            )
            response = AiCallQueryResponse(
                items=[AiCallListItem.from_trace(item) for item in page],
                next_cursor=next_cursor,
                metadata={
                    "retained": len(self._items),
                    "matched": matched_count,
                    "bounded": True,
                    "max_items": self._max_items,
                },
            )
            assert_redacted_artifact(response)
            return response

    def get(self, call_id: str) -> AiCallTrace | None:
        with self._lock:
            trace = self._items.get(call_id)
            if trace is not None:
                assert_redacted_artifact(trace)
            return trace

    def _load(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            return
        migrated = False
        with self._path.open(encoding="utf-8") as handle:
            lines = handle.readlines()
            for index, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    if index == len(lines) - 1:
                        break
                    raise
                assert_redacted_artifact(raw)
                raw, was_migrated = self._migrate_legacy_role(raw)
                migrated = migrated or was_migrated
                trace = AiCallTrace.model_validate(raw)
                if trace.call_id not in self._items and len(self._items) >= self._max_items:
                    self._items.popitem(last=False)
                self._items[trace.call_id] = trace
                self._writes_since_compaction += 1
        if migrated or self._interrupt_incomplete():
            self._persist()
        elif self._writes_since_compaction >= self._max_items * 4:
            self._persist()

    @staticmethod
    def _migrate_legacy_role(raw: object) -> tuple[object, bool]:
        if not isinstance(raw, dict) or raw.get("role") != "director":
            return raw, False
        return {**raw, "role": "legacy_director"}, True

    def _interrupt_incomplete(self) -> bool:
        interrupted_at_ms = self._clock_ms()
        changed = False
        for call_id, trace in tuple(self._items.items()):
            if trace.status in _TERMINAL_STATUSES:
                continue
            started_at_ms = trace.started_at_ms
            self._items[call_id] = trace.model_copy(
                update={
                    "status": AiCallStatus.INTERRUPTED,
                    "updated_at_ms": interrupted_at_ms,
                    "completed_at_ms": interrupted_at_ms,
                    "duration_ms": max(0, interrupted_at_ms - started_at_ms),
                    "timeline": [
                        *trace.timeline[-255:],
                        AiCallTimelineEvent(
                            stage=AiCallStatus.INTERRUPTED,
                            at_ms=interrupted_at_ms,
                            detail={"reason": "backend_restart"},
                        ),
                    ],
                }
            )
            changed = True
        return changed

    def _persist(self) -> None:
        if self._path is None:
            return
        payloads = [item.model_dump(mode="json") for item in self._items.values()]
        assert_redacted_artifact(payloads)
        lines = [
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for payload in payloads
        ]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._path, "\n".join(lines) + ("\n" if lines else ""))
        self._writes_since_compaction = 0

    def _append(self, trace: AiCallTrace) -> None:
        assert self._path is not None
        payload = trace.model_dump(mode="json")
        assert_redacted_artifact(payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            handle.write("\n")
        self._writes_since_compaction += 1

    @staticmethod
    def _matches(trace: AiCallTrace, query: AiCallQuery) -> bool:
        return (
            (query.session_id is None or trace.session_id == query.session_id)
            and (query.role is None or trace.role is query.role)
            and (query.status is None or trace.status is query.status)
            and (
                query.correlation_id is None
                or trace.correlation_id == query.correlation_id
            )
        )

    @staticmethod
    def _sort_key(trace: AiCallTrace) -> tuple[int, str]:
        return trace.started_at_ms, trace.call_id

    def _ordered_descending(self) -> tuple[AiCallTrace, ...]:
        if self._ordered_items is None:
            self._ordered_items = tuple(
                sorted(
                    self._items.values(),
                    key=self._sort_key,
                    reverse=True,
                )
            )
        return self._ordered_items

    @staticmethod
    def _encode_cursor(trace: AiCallTrace) -> str:
        payload = json.dumps(
            {
                "started_at_ms": trace.started_at_ms,
                "call_id": trace.call_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> tuple[int, str] | None:
        if cursor is None:
            return None
        try:
            decoded = base64.b64decode(
                cursor.encode("ascii"),
                altchars=b"-_",
                validate=True,
            ).decode("utf-8")
            value = json.loads(decoded)
        except (
            binascii.Error,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
        ) as error:
            raise ValueError("invalid AI call cursor") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"started_at_ms", "call_id"}
            or not isinstance(value["started_at_ms"], int)
            or value["started_at_ms"] < 0
            or not isinstance(value["call_id"], str)
            or not value["call_id"]
        ):
            raise ValueError("invalid AI call cursor")
        return value["started_at_ms"], value["call_id"]

    @staticmethod
    def _atomic_write(destination: Path, content: str) -> None:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            delete=False,
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        os.replace(temporary, destination)
