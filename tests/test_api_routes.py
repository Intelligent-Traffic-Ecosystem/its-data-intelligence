from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_endpoint():
    """Health endpoint should respond even if Postgres is unavailable."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "postgres" in data


def test_cameras_endpoint():
    response = client.get("/cameras")
    # Should return 200 even with empty database
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_congestion_current():
    response = client.get("/congestion/current")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_metrics_current_missing_param():
    response = client.get("/metrics/current")
    # Should return 422 — camera_id is required
    assert response.status_code == 422
