from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_endpoint():
    """Health endpoint should respond even if Kafka is unavailable."""
    with patch("api.routes.health._check_kafka", return_value="ok"), \
         patch("api.routes.health.SessionLocal") as mock_session:
        mock_db = mock_session.return_value
        mock_db.execute.return_value = None
        response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["kafka"] == "ok"
    assert "postgres" in data


def test_health_endpoint_kafka_down():
    """Health returns degraded but still 200 when Kafka is unreachable."""
    with patch("api.routes.health._check_kafka", return_value="unreachable"):
        response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["kafka"] == "unreachable"


def test_cameras_endpoint():
    response = client.get("/cameras")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_congestion_current():
    response = client.get("/congestion/current")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_metrics_current_missing_param():
    response = client.get("/metrics/current")
    assert response.status_code == 422
