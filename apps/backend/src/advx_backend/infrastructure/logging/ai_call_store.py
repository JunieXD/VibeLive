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
    AiCallQuery,
    AiCallQueryResponse,
    AiCallStatus,
    AiCallTimelineEvent,
    AiCallTrace,
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
        self._writes_since_compaction = 0
        self._lock = RLock()
        if path is not None:
            self._load()

    def upsert(self, trace: AiCallTrace) -> None:
        with self._lock:
            assert_redacted_artifact(trace)
            evicted = (
                trace.call_id not in self._items
                and len(self._items) >= self._max_items
            )
            if evicted:
                self._items.popitem(last=False)
            self._items[trace.call_id] = trace
            if self._path is None:
                return
            if evicted or self._writes_since_compaction >= self._max_items * 4:
                self._persist()
                return
            self._append(trace)

    def query(self, query: AiCallQuery | None = None) -> AiCallQueryResponse:
        with self._lock:
            query = query or AiCallQuery()
            matching = sorted(
                (
                    item
                    for item in self._items.values()
                    if self._matches(item, query)
                ),
                key=self._sort_key,
                reverse=True,
            )
            matched_count = len(matching)
            cursor_key = self._decode_cursor(query.cursor)
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
                items=page,
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

    def _load(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            return
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
                trace = AiCallTrace.model_validate(raw)
                if trace.call_id not in self._items and len(self._items) >= self._max_items:
                    self._items.popitem(last=False)
                self._items[trace.call_id] = trace
                self._writes_since_compaction += 1
        if self._interrupt_incomplete():
            self._persist()
        elif self._writes_since_compaction >= self._max_items * 4:
            self._persist()

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
