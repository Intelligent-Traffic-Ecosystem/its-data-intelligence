"""Tests for /api/dashboard/* and /api/map/* endpoints (issue #34)."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from api.main import app
from shared.models import TrafficEvent, TrafficMetric
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
        db.query(TrafficEvent).delete()
        db.commit()
    finally:
        db.close()


def test_dashboard_summary_empty():
    _clear()
    res = client.get("/api/dashboard/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["total_cameras_active"] == 0
    assert body["total_vehicles_last_window"] == 0
    assert body["worst_camera"] is None


def test_dashboard_summary_aggregates():
    _clear()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed(
        [
            TrafficMetric(
                camera_id="cam_a",
                window_start=now,
                window_end=now + timedelta(seconds=5),
                vehicle_count=10,
                counts_by_class=json.dumps({"car": 10}),
                avg_speed_kmh=20.0,
                stopped_ratio=0.1,
                queue_length=2,
                congestion_level="MODERATE",
                congestion_score=0.5,
            ),
            TrafficMetric(
                camera_id="cam_b",
                window_start=now,
                window_end=now + timedelta(seconds=5),
                vehicle_count=30,
                counts_by_class=json.dumps({"car": 25, "bus": 5}),
                avg_speed_kmh=5.0,
                stopped_ratio=0.6,
                queue_length=12,
                congestion_level="SEVERE",
                congestion_score=0.92,
            ),
        ]
    )

    res = client.get("/api/dashboard/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["total_cameras_active"] == 2
    assert body["total_vehicles_last_window"] == 40
    assert body["average_speed_kmh"] == 12.5
    assert body["congestion_breakdown"]["SEVERE"] == 1
    assert body["worst_camera"]["camera_id"] == "cam_b"


def test_dashboard_events_returns_latest():
    _clear()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed(
        [
            TrafficEvent(
                camera_id="cam_a",
                ts=now - timedelta(seconds=i),
                vehicle_id=f"v{i}",
                vehicle_class="car",
                speed_kmh=40.0,
                confidence=0.9,
            )
            for i in range(15)
        ]
    )
    res = client.get("/api/dashboard/events?limit=5")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 5
    assert body[0]["vehicle_id"] == "v0"


def test_map_heatmap_filters_window():
    _clear()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed(
        [
            TrafficMetric(
                camera_id="cam_a",
                window_start=now - timedelta(minutes=2),
                window_end=now - timedelta(minutes=2) + timedelta(seconds=5),
                vehicle_count=12,
                counts_by_class=json.dumps({"car": 12}),
                avg_speed_kmh=15,
                stopped_ratio=0.1,
                queue_length=3,
                congestion_level="MODERATE",
                congestion_score=0.5,
            ),
            TrafficMetric(
                camera_id="cam_old",
                window_start=now - timedelta(minutes=30),
                window_end=now - timedelta(minutes=30) + timedelta(seconds=5),
                vehicle_count=5,
                counts_by_class=json.dumps({"car": 5}),
                avg_speed_kmh=40,
                stopped_ratio=0.0,
                queue_length=0,
                congestion_level="LOW",
                congestion_score=0.1,
            ),
        ]
    )
    res = client.get("/api/map/heatmap?minutes=5")
    assert res.status_code == 200
    body = res.json()
    assert body["window_minutes"] == 5
    cams = [p["camera_id"] for p in body["points"]]
    assert "cam_a" in cams
    assert "cam_old" not in cams


def test_map_incidents_default_only_high_severe():
    _clear()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed(
        [
            TrafficMetric(
                camera_id="cam_low",
                window_start=now,
                window_end=now + timedelta(seconds=5),
                vehicle_count=2,
                counts_by_class=json.dumps({"car": 2}),
                avg_speed_kmh=50,
                stopped_ratio=0.0,
                queue_length=0,
                congestion_level="LOW",
                congestion_score=0.1,
            ),
            TrafficMetric(
                camera_id="cam_severe",
                window_start=now,
                window_end=now + timedelta(seconds=5),
                vehicle_count=40,
                counts_by_class=json.dumps({"car": 40}),
                avg_speed_kmh=2,
                stopped_ratio=0.8,
                queue_length=20,
                congestion_level="SEVERE",
                congestion_score=0.95,
            ),
        ]
    )
    res = client.get("/api/map/incidents")
    assert res.status_code == 200
    body = res.json()
    cams = [i["camera_id"] for i in body]
    assert cams == ["cam_severe"]
