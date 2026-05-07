from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from shared.schemas import AnalyticsRangeSummary

client = TestClient(app)


def test_health_endpoint():
    """Health endpoint should respond even if Kafka is unavailable."""
    with (
        patch("api.routes.health._check_kafka", return_value="ok"),
        patch("api.routes.health.SessionLocal") as mock_session,
    ):
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


def test_analytics_metrics_endpoint():
    response = client.get("/api/analytics/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert "average_congestion" in payload
    assert "peak_hour_trends" in payload
    assert "top_segments" in payload
    assert "incidents" in payload


def test_analytics_compare_endpoint():
    params = {
        "start_a": "2026-01-01T00:00:00Z",
        "end_a": "2026-01-02T00:00:00Z",
        "start_b": "2026-01-03T00:00:00Z",
        "end_b": "2026-01-04T00:00:00Z",
    }
    response = client.get("/api/analytics/compare", params=params)
    assert response.status_code == 200
    payload = response.json()
    assert "range_a" in payload
    assert "range_b" in payload
    assert "congestion_delta" in payload


def test_analytics_report_pdf_endpoint():
    with patch("api.routes.analytics._range_summary") as mock_summary:
        mock_summary.return_value = AnalyticsRangeSummary(
            start="2026-01-01T00:00:00Z",
            end="2026-01-01T23:59:59Z",
            average_congestion=0.45,
            vehicle_count=123,
            average_speed_kmh=35.2,
            incident_count=7,
            peak_hour=18,
        )
        response = client.get("/api/analytics/report/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment; filename=" in response.headers["content-disposition"]
