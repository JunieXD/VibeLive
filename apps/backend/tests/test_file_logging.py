import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from advx_backend.application.runtime_capability_probe import RuntimeCapabilityProbeBlockedError
from advx_backend.application.runtime_session_service import _log_capability_probe_failure
from advx_backend.infrastructure.logging.configure import configure_logging
from advx_backend.providers.model.base import CapabilityProbeCheck, CapabilityProbeStatus


def _file_handler() -> RotatingFileHandler:
    handlers = logging.getLogger("advx_backend").handlers
    return next(
        handler
        for handler in handlers
        if isinstance(handler, RotatingFileHandler)
        and getattr(handler, "advx_backend_file_handler", False)
    )


def test_file_logging_records_redacted_capability_probe_diagnostics(tmp_path: Path) -> None:
    configure_logging(log_directory=tmp_path / "logs")
    error = RuntimeCapabilityProbeBlockedError(
        status=CapabilityProbeStatus.BLOCKED,
        checks=(
            CapabilityProbeCheck(
                capability="memory_json_output",
                status=CapabilityProbeStatus.BLOCKED,
                model_id="sk-memory-secret-key",
                error_code="upstream_http_error",
                http_status=429,
            ),
        ),
    )

    _log_capability_probe_failure(
        error,
        operation="start",
        client_request_id="desktop-request-1",
        provider_profile_id="default",
    )
    logging.getLogger("advx_backend.tests.file_logging").warning(
        "provider returned Authorization: Bearer sk-example-secret-token"
    )
    handler = _file_handler()
    handler.flush()

    path = tmp_path / "logs" / "backend.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert handler.maxBytes == 2 * 1024 * 1024
    assert handler.backupCount == 5
    assert entries[0] == {
        "capability_checks": [
            {
                "capability": "memory_json_output",
                "error_code": "upstream_http_error",
                "http_status": 429,
                "model_id": "[REDACTED_SECRET]",
                "status": "blocked",
            }
        ],
        "client_request_id": "desktop-request-1",
        "level": "WARNING",
        "logger": "advx_backend.application.runtime_session_service",
        "message": "runtime.capability_probe.rejected",
        "operation": "start",
        "provider_profile_id": "default",
        "timestamp": entries[0]["timestamp"],
    }
    assert entries[1]["message"] == "provider returned Authorization: Bearer [REDACTED]"
    assert "sk-example-secret-token" not in path.read_text(encoding="utf-8")


def test_file_logging_records_session_stop_lifecycle(tmp_path: Path) -> None:
    configure_logging(log_directory=tmp_path / "logs")
    logging.getLogger("advx_backend.application.session_service").info(
        "session.stop.completed",
        extra={"session_id": "session-123", "outcome": "completed"},
    )
    handler = _file_handler()
    handler.flush()

    path = tmp_path / "logs" / "backend.jsonl"
    [entry] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert entry == {
        "level": "INFO",
        "logger": "advx_backend.application.session_service",
        "message": "session.stop.completed",
        "outcome": "completed",
        "session_id": "session-123",
        "timestamp": entry["timestamp"],
    }
