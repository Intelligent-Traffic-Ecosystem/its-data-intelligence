"""Tests for /api/predict/congestion and the forecaster baseline (issue #30)."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from api.main import app
from processor.forecaster import forecast
from shared.models import TrafficMetric
from tests.conftest import _TestingSessionLocal

client = TestClient(app)


def _seed(rows):
    db = _TestingSessionLocal()
    try:
        for r in rows:
            db.add(r)
        db.commit()
    finally:
        db.close()


def _clear():
    db = _TestingSessionLocal()
    try:
        db.query(TrafficMetric).delete()
        db.commit()
    finally:
        db.close()


def test_forecaster_handles_empty():
    out = forecast([], horizon=3)
    assert len(out) == 3
    assert all(p.score == 0.0 and p.level == "LOW" for p in out)


def test_forecaster_projects_rising_trend():
    """Monotonically increasing history must produce non-decreasing forecast."""
    history = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    points = forecast(history, horizon=4, trend_lookback=6)
    scores = [p.score for p in points]
    assert scores == sorted(scores)
    assert scores[0] >= 0.5  # next step should be at least near the latest level


def test_forecaster_clamps_to_unit_interval():
    history = [0.95] * 10  # already near saturation
    points = forecast(history, horizon=5)
    assert all(0.0 <= p.score <= 1.0 for p in points)


def test_predict_endpoint_404_when_no_history():
    _clear()
    res = client.get("/api/predict/congestion?camera_id=cam_unknown")
    assert res.status_code == 404


def test_predict_endpoint_returns_horizon_payload():
    _clear()
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
    rows = []
    # 10 minutes of 5-second windows = 120 samples; fewer is fine for the test.
    for i in range(20):
        ws = base + timedelta(seconds=5 * i)
        rows.append(
            TrafficMetric(
                camera_id="cam_predict",
                window_start=ws,
                window_end=ws + timedelta(seconds=5),
                vehicle_count=5 + i,
                counts_by_class=json.dumps({"car": 5 + i}),
                avg_speed_kmh=40.0 - i,
                stopped_ratio=0.05 * i / 5,
                queue_length=i,
                congestion_level="MODERATE",
                congestion_score=min(0.05 * i, 0.95),
            )
        )
    _seed(rows)
    res = client.get(
        "/api/predict/congestion?camera_id=cam_predict&horizon_minutes=1"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["camera_id"] == "cam_predict"
    assert body["history_samples"] == 20
    assert len(body["forecast"]) > 0
    first = body["forecast"][0]
    assert "predicted_at" in first
    assert first["congestion_level"] in {"LOW", "MODERATE", "HIGH", "SEVERE"}
    assert 0.0 <= first["congestion_score"] <= 1.0
