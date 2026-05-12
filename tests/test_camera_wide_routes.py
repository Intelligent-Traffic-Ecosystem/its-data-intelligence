import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.main import app
from conftest import _TestingSessionLocal
from shared.models import TrafficMetric

client = TestClient(app)


def _clear():
    db = _TestingSessionLocal()
    try:
        db.query(TrafficMetric).delete()
        db.commit()
    finally:
        db.close()


def _metric(
    *,
    id: int,
    camera_id: str,
    window_start: datetime,
    lane_id: int | None,
    vehicle_count: int,
    congestion_score: float,
):
    return TrafficMetric(
        id=id,
        camera_id=camera_id,
        window_start=window_start,
        window_end=window_start + timedelta(seconds=5),
        lane_id=lane_id,
        vehicle_count=vehicle_count,
        counts_by_class=json.dumps({"car": vehicle_count}),
        avg_speed_kmh=30.0,
        stopped_ratio=0.0,
        queue_length=0,
        congestion_level="LOW",
        congestion_score=congestion_score,
    )


def _seed(*rows: TrafficMetric):
    db = _TestingSessionLocal()
    try:
        db.add_all(rows)
        db.commit()
    finally:
        db.close()


def test_metric_history_returns_camera_wide_rows_only():
    _clear()
    start = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
    _seed(
        _metric(
            id=1,
            camera_id="cam_03",
            window_start=start,
            lane_id=None,
            vehicle_count=12,
            congestion_score=0.4,
        ),
        _metric(
            id=2,
            camera_id="cam_03",
            window_start=start,
            lane_id=1,
            vehicle_count=8,
            congestion_score=0.7,
        ),
    )

    response = client.get(
        "/metrics/history",
        params={
            "camera_id": "cam_03",
            "from": start.isoformat(),
            "to": (start + timedelta(minutes=1)).isoformat(),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["lane_id"] is None
    assert body[0]["vehicle_count"] == 12


def test_current_congestion_returns_camera_wide_rows_only():
    _clear()
    start = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
    _seed(
        _metric(
            id=3,
            camera_id="cam_03",
            window_start=start,
            lane_id=None,
            vehicle_count=12,
            congestion_score=0.4,
        ),
        _metric(
            id=4,
            camera_id="cam_03",
            window_start=start,
            lane_id=1,
            vehicle_count=8,
            congestion_score=0.7,
        ),
    )

    response = client.get("/congestion/current")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["camera_id"] == "cam_03"
    assert body[0]["lane_id"] is None
    assert body[0]["vehicle_count"] == 12


def test_analytics_metrics_ignore_lane_rows():
    _clear()
    start = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
    _seed(
        _metric(
            id=5,
            camera_id="cam_03",
            window_start=start,
            lane_id=None,
            vehicle_count=12,
            congestion_score=0.4,
        ),
        _metric(
            id=6,
            camera_id="cam_03",
            window_start=start,
            lane_id=1,
            vehicle_count=99,
            congestion_score=0.95,
        ),
    )

    response = client.get(
        "/api/analytics/metrics",
        params={
            "start": start.isoformat(),
            "end": (start + timedelta(minutes=1)).isoformat(),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["average_congestion"] == 0.4
    assert body["peak_hour_trends"][0]["vehicle_count"] == 12
