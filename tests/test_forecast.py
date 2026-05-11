"""Tests for /api/predict/congestion and the ST-GCN bridge."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app
from processor.forecaster import forecast
from shared.models import TrafficMetric
from shared.stgcn_predictor import reset_predictor_for_tests
from conftest import _TestingSessionLocal

client = TestClient(app)


@pytest.fixture
def mock_stgcn_predictor(monkeypatch):
    """Avoid loading Torch / checkpoint during API tests."""

    class _FakePredictor:
        _cfg = SimpleNamespace(
            features=SimpleNamespace(window_size_seconds=5),
        )

        def predict(self, lookback_minutes=None):
            base = datetime.now(timezone.utc).replace(microsecond=0)
            n = 120
            probs = [min(0.05 + 0.002 * i, 0.95) for i in range(n)]
            ts = [base + timedelta(seconds=5 * (i + 1)) for i in range(n)]

            def lane(lid: int, bump: float):
                return SimpleNamespace(
                    camera_id="cam_predict",
                    lane_id=lid,
                    congestion_probabilities=[min(p + bump, 1.0) for p in probs],
                    forecast_timestamps=ts,
                )

            return [lane(1, 0.0), lane(2, 0.02)]

    def _fake_get_predictor(app_settings=None):
        return _FakePredictor()

    monkeypatch.setattr(
        "shared.stgcn_predictor.get_predictor",
        _fake_get_predictor,
    )
    reset_predictor_for_tests()
    yield
    reset_predictor_for_tests()


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


def test_predict_endpoint_404_when_no_history(mock_stgcn_predictor):
    _clear()
    res = client.get("/api/predict/congestion?camera_id=cam_unknown")
    assert res.status_code == 404


def test_predict_endpoint_returns_horizon_payload(mock_stgcn_predictor):
    _clear()
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
    rows = []
    for i in range(20):
        ws = base + timedelta(seconds=5 * i)
        rows.append(
            TrafficMetric(
                id=i + 1,
                camera_id="cam_predict",
                lane_id=1,
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
    assert body["model"] == "stgcn"
    assert body["history_samples"] == 20
    assert len(body["forecast"]) > 0
    first = body["forecast"][0]
    assert "predicted_at" in first
    assert first["congestion_level"] in {"LOW", "MODERATE", "HIGH", "SEVERE"}
    assert 0.0 <= first["congestion_score"] <= 1.0


def test_predict_endpoint_default_horizon_lookback(mock_stgcn_predictor):
    _clear()
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=2)
    rows = [
        TrafficMetric(
            id=1,
            camera_id="cam_predict",
            lane_id=1,
            window_start=base,
            window_end=base + timedelta(seconds=5),
            vehicle_count=3,
            counts_by_class=json.dumps({"car": 3}),
            avg_speed_kmh=35.0,
            stopped_ratio=0.1,
            queue_length=0,
            congestion_level="LOW",
            congestion_score=0.2,
        )
    ]
    _seed(rows)
    res = client.get("/api/predict/congestion?camera_id=cam_predict")
    assert res.status_code == 200
    body = res.json()
    assert body["horizon_minutes"] == 10
    # lookback is a query default; not echoed in payload — step count reflects 10 min at 5s
    assert body["step_seconds"] == 5
    assert len(body["forecast"]) == (10 * 60) // 5  # 120 steps, capped by fake horizon
