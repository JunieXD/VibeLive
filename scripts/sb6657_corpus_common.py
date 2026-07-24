"""Shared deterministic I/O helpers for the sb6657 corpus scripts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_barrage: dict[str, dict[str, Any]] = {}
    for record in records:
        item = dict(record)
        barrage = item["barrage"]
        current = by_barrage.get(barrage)
        if current is None or _record_choice_key(item) < _record_choice_key(current):
            by_barrage[barrage] = item
    return sorted(by_barrage.values(), key=lambda item: (item["barrage"], _stable_id(item)))


def jsonl_bytes(records: Iterable[Mapping[str, Any]]) -> bytes:
    lines = (canonical_json(record) for record in records)
    text = "\n".join(lines)
    return (text + "\n").encode("utf-8") if text else b""


def canonical_sha256(records: Iterable[Mapping[str, Any]]) -> str:
    return hashlib.sha256(jsonl_bytes(canonical_records(records))).hexdigest()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def _stable_id(record: Mapping[str, Any]) -> int:
    value = record.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else 2**63 - 1


def _count(record: Mapping[str, Any]) -> int:
    value = record.get("cnt", 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _record_choice_key(record: Mapping[str, Any]) -> tuple[int, int, str]:
    # Keep the most copied duplicate, then use stable source fields as tie breakers.
    return (-_count(record), _stable_id(record), canonical_json(record))
