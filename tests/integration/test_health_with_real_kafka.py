"""/health reports kafka=ok and postgres=ok when both containers are up."""

from __future__ import annotations


def test_health_reports_both_subsystems(kafka_container, fresh_db):
    from fastapi.testclient import TestClient

    from api.main import app
    from api.routes import health as health_route

    health_route._kafka_admin = None

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["postgres"] == "ok"
    assert data["kafka"] == "ok"
    assert data["status"] == "ok"
