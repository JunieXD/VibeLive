from pathlib import Path

from fastapi.testclient import TestClient

from advx_backend.bootstrap import build_runtime
from advx_backend.main import app, create_app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "protocol_version": 3}


def test_health_reports_machine_readable_migration_failure(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(data_directory=tmp_path)

    def fail_upgrade() -> None:
        raise RuntimeError("synthetic migration failure")

    runtime.database._upgrade_schema = fail_upgrade  # type: ignore[method-assign]
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["persistence_error"]["code"] == "sqlite_migration_failed"


def test_health_reports_corrupt_database_as_degraded(tmp_path: Path) -> None:
    (tmp_path / "advx.sqlite3").write_bytes(b"corrupt sqlite")
    runtime = build_runtime(data_directory=tmp_path)

    with TestClient(create_app(runtime=runtime)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["persistence_error"]["code"] == "sqlite_validation_failed"
    assert response.json()["persistence_error"]["backup_path"] == ""


def test_health_reports_startup_recovery_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = build_runtime(data_directory=tmp_path)

    async def fail_recovery(*, ended_at_ms: int) -> int:
        del ended_at_ms
        raise RuntimeError("recovery write failed")

    monkeypatch.setattr(
        runtime.session_record_store,
        "recover_interrupted",
        fail_recovery,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        response = client.get("/health")

    assert response.json()["status"] == "degraded"
    assert response.json()["persistence_error"]["code"] == "sqlite_recovery_failed"
    assert runtime.database.started is False
