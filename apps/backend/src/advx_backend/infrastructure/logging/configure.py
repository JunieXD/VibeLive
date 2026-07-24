import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FILE_NAME = "backend.jsonl"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5
_MANAGED_HANDLER = "advx_backend_file_handler"
_SAFE_EXTRA_FIELDS = (
    "apply_id",
    "capability_checks",
    "client_request_id",
    "operation",
    "provider_profile_id",
    "session_id",
)
_INLINE_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_SECRET]"),
    (
        re.compile(
            r"(?i)\b[a-z0-9._-]*(?:api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|client[_-]?secret|password|credential|secret)"
            r"[a-z0-9._-]*\s*[:=]\s*[^\s,;}\]]+"
        ),
        "[REDACTED_SECRET]",
    ),
)


class JsonLineFormatter(logging.Formatter):
    """Emit a small, redacted set of structured fields for local diagnostics."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
        }
        for field in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = _redact_value(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _redact(value: str) -> str:
    for pattern, replacement in _INLINE_SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def configure_logging(
    *,
    log_directory: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Configure console logging plus a bounded JSONL file for backend diagnostics."""

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    backend_logger = logging.getLogger("advx_backend")
    backend_logger.setLevel(level)
    if log_directory is None:
        return

    path = (log_directory / _FILE_NAME).resolve()
    log_directory.mkdir(parents=True, exist_ok=True)
    for handler in tuple(backend_logger.handlers):
        if getattr(handler, _MANAGED_HANDLER, False):
            if Path(handler.baseFilename) == path:
                handler.setLevel(level)
                return
            backend_logger.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    setattr(handler, _MANAGED_HANDLER, True)
    handler.setLevel(level)
    handler.setFormatter(JsonLineFormatter())
    backend_logger.addHandler(handler)
