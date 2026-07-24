import base64
import binascii
import json
import os
import re
from collections import deque
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pydantic import BaseModel

from advx_backend.contracts.debug import (
    DebugTrace,
    ObservationWaveTrace,
    TraceQuery,
    TraceQueryResponse,
    ViewerRequestTrace,
)

_FORBIDDEN_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "audio",
    "base64",
    "client_secret",
    "choices",
    "credential",
    "credentials",
    "frame",
    "frames",
    "image",
    "image_url",
    "input_text",
    "instructions",
    "media",
    "messages",
    "password",
    "prompt",
    "prompt_text",
    "provider_response",
    "provider_raw_response",
    "raw_media",
    "raw_prompt",
    "raw_input",
    "raw_response",
    "refresh_token",
    "response_body",
    "secret",
    "system_prompt",
    "token",
}
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"(?i)\b[a-z0-9._-]*(?:api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|client[_-]?secret|password|credential|secret)"
        r"[a-z0-9._-]*\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)^data:(?:audio|image|video)/"),
)
_FORBIDDEN_KEY_MARKERS = {
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "password",
    "refreshtoken",
    "secret",
}


class UnsafeTraceArtifactError(ValueError):
    """Raised when a debug artifact contains data that must never be persisted."""


def assert_redacted_artifact(value: Any) -> None:
    """Reject secrets, raw media, full prompts, and raw provider responses."""

    _scan_artifact(_json_value(value), path="$")


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _scan_artifact(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        normalized_keys = {
            str(key).strip().lower().replace("-", "_")
            for key in value
        }
        if {
            "memory_id",
            "room_id",
            "memory_type",
            "content",
        }.issubset(normalized_keys):
            raise UnsafeTraceArtifactError(
                f"long-term memory content is forbidden at {path}.content"
            )
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            compact = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _FORBIDDEN_KEYS or any(
                marker in compact for marker in _FORBIDDEN_KEY_MARKERS
            ):
                raise UnsafeTraceArtifactError(f"forbidden artifact field at {path}.{key}")
            _scan_artifact(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_artifact(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _SENSITIVE_VALUE_PATTERNS:
            if pattern.search(value):
                raise UnsafeTraceArtifactError(f"sensitive artifact value at {path}")


class TraceStore:
    """Bounded trace storage with optional durable JSONL persistence."""

    def __init__(self, *, max_items: int = 1_000, path: Path | None = None) -> None:
        if max_items < 1:
            raise ValueError("max_items must be at least one")
        self._max_items = max_items
        self._path = path
        self._items: deque[DebugTrace] = deque(maxlen=max_items)
        if path is not None:
            self._load()

    def append(self, trace: DebugTrace) -> None:
        assert_redacted_artifact(trace)
        self._items.append(trace)
        self._persist()

    def query(self, query: TraceQuery | None = None) -> TraceQueryResponse:
        query = query or TraceQuery()
        matching = [item for item in self._items if self._matches(item, query)]
        offset = self._decode_cursor(query.cursor)
        page = matching[offset : offset + query.limit]
        next_offset = offset + len(page)
        next_cursor = self._encode_cursor(next_offset) if next_offset < len(matching) else None
        response = TraceQueryResponse(
            items=[item for item in page if isinstance(item, ViewerRequestTrace)],
            waves=[item for item in page if isinstance(item, ObservationWaveTrace)],
            next_cursor=next_cursor,
            metadata={
                "retained": len(self._items),
                "matched": len(matching),
                "bounded": True,
                "max_items": self._max_items,
            },
        )
        assert_redacted_artifact(response)
        return response

    def export(self, destination: Path, query: TraceQuery | None = None) -> int:
        response = self.query(query or TraceQuery(limit=1_000))
        payload = response.model_dump(mode="json")
        assert_redacted_artifact(payload)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            destination,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n",
        )
        return len(response.items) + len(response.waves)

    def _load(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            return
        loaded: deque[DebugTrace] = deque(maxlen=self._max_items)
        migrated = False
        with self._path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                assert_redacted_artifact(raw)
                if raw.get("trace_kind") == "observation_wave":
                    raw, was_migrated = self._migrate_observation_wave_trace(raw)
                    migrated = migrated or was_migrated
                    loaded.append(ObservationWaveTrace.model_validate(raw))
                else:
                    raw, was_migrated = self._migrate_viewer_request_trace(raw)
                    migrated = migrated or was_migrated
                    loaded.append(ViewerRequestTrace.model_validate(raw))
        self._items = loaded
        if migrated:
            self._persist()

    @staticmethod
    def _migrate_observation_wave_trace(raw: Any) -> tuple[Any, bool]:
        if not isinstance(raw, dict) or "director_status" not in raw:
            return raw, False

        migrated = dict(raw)
        migrated.setdefault("status", migrated["director_status"])
        del migrated["director_status"]
        return migrated, True

    @staticmethod
    def _migrate_viewer_request_trace(raw: Any) -> tuple[Any, bool]:
        if not isinstance(raw, dict):
            return raw, False

        migrated = dict(raw)
        was_migrated = False
        if "director_decision" in migrated:
            migrated.setdefault("decision", migrated["director_decision"])
            del migrated["director_decision"]
            was_migrated = True
        if "director_budget" in migrated:
            del migrated["director_budget"]
            was_migrated = True
        return migrated, was_migrated

    def _persist(self) -> None:
        if self._path is None:
            return
        lines = [
            json.dumps(
                item.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for item in self._items
        ]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._path, "\n".join(lines) + ("\n" if lines else ""))

    @staticmethod
    def _matches(trace: DebugTrace, query: TraceQuery) -> bool:
        return (
            (query.room_id is None or trace.room_id == query.room_id)
            and (query.session_id is None or trace.session_id == query.session_id)
            and (
                query.observation_id is None
                or trace.observation_id == query.observation_id
            )
            and (
                query.viewer_instance_id is None
                or (
                    isinstance(trace, ViewerRequestTrace)
                    and trace.viewer_instance_id == query.viewer_instance_id
                )
            )
            and (
                query.response_status is None
                or (
                    isinstance(trace, ViewerRequestTrace)
                    and trace.response_status is query.response_status
                )
            )
        )

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            value = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
        except (binascii.Error, ValueError, UnicodeDecodeError) as error:
            raise ValueError("invalid trace cursor") from error
        if value < 0:
            raise ValueError("invalid trace cursor")
        return value

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
