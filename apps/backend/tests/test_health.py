from fastapi.testclient import TestClient

from advx_backend.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "protocol_version": 1}
