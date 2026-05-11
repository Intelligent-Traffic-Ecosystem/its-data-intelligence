import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.main import app
from shared.config import settings
from shared.models import AlertRecord, CameraRegistry, TrafficMetric
from tests.conftest import _TestingSessionLocal

client = TestClient(app)
ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token", "X-Admin-User": "b3-admin"}
settings.admin_api_key = "test-admin-token"


def _clear():
    db = _TestingSessionLocal()
    try:
        db.query(AlertRecord).delete()
        db.query(TrafficMetric).delete()
        db.query(CameraRegistry).delete()
        db.commit()
    finally:
        db.close()


def test_admin_camera_registry_crud():
    _clear()
    create_payload = {
        "camera_id": "cam_101",
        "name": "Junction North",
        "latitude": 6.9271,
        "longitude": 79.8612,
        "road_segment": "A1-N",
        "description": "Primary feed",
    }
    created = client.post("/api/admin/cameras", json=create_payload, headers=ADMIN_HEADERS)
    assert created.status_code == 201
    assert created.json()["camera_id"] == "cam_101"

    listed = client.get("/api/admin/cameras", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    updated = client.put(
        "/api/admin/cameras/cam_101",
        json={"road_segment": "A1-N-UPD"},
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["road_segment"] == "A1-N-UPD"

    deleted = client.delete("/api/admin/cameras/cam_101", headers=ADMIN_HEADERS)
    assert deleted.status_code == 204


def test_admin_camera_put_accepts_lat_lng_aliases_and_upserts():
    _clear()

    updated = client.put(
        "/api/admin/cameras/cam_01",
        json={"lat": 6.9271, "lng": 79.8612, "road_segment": "COL-01"},
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["camera_id"] == "cam_01"
    assert body["latitude"] == 6.9271
    assert body["longitude"] == 79.8612

    listed = client.get("/api/admin/cameras", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    assert listed.json()[0]["camera_id"] == "cam_01"


def test_admin_camera_list_allows_null_coordinates():
    _clear()
    db = _TestingSessionLocal()
    try:
        db.add(
            CameraRegistry(
                camera_id="cam_null",
                name="Null Coord Camera",
                latitude=None,
                longitude=None,
                road_segment="SEG-X",
            )
        )
        db.commit()
    finally:
        db.close()

    listed = client.get("/api/admin/cameras", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    body = listed.json()
    assert body[0]["camera_id"] == "cam_null"
    assert body[0]["latitude"] is None
    assert body[0]["longitude"] is None


def test_alert_history_ack_and_export():
    _clear()
    now = datetime.now(UTC).replace(microsecond=0)
    db = _TestingSessionLocal()
    try:
        db.add(
            AlertRecord(
                severity="WARNING",
                alert_type="CONGESTION",
                camera_id="cam_1",
                road_segment="SEG-A",
                title="Warning congestion",
                message="Warning congestion on SEG-A",
                congestion_level="HIGH",
                congestion_score=0.84,
                triggered_at=now - timedelta(minutes=2),
                payload=json.dumps({"source": "test"}),
            )
        )
        db.commit()
        alert_id = db.query(AlertRecord).first().id
    finally:
        db.close()

    history = client.get("/api/alerts/history", params={"camera_id": "cam_1"})
    assert history.status_code == 200
    assert len(history.json()) == 1

    ack = client.post(
        f"/api/alerts/{alert_id}/acknowledge",
        json={"admin_id": "b3-admin"},
        headers=ADMIN_HEADERS,
    )
    assert ack.status_code == 200
    assert ack.json()["status"] in {"acknowledged", "already_acknowledged"}

    export = client.get("/api/alerts/export", params={"road_segment": "SEG-A"})
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "alert_id,camera_id,road_segment" in export.text


def test_map_heatmap_uses_registry_coordinates_and_weight():
    _clear()
    now = datetime.now(UTC).replace(microsecond=0)
    db = _TestingSessionLocal()
    try:
        db.add_all(
            [
                CameraRegistry(
                    camera_id="cam_h1",
                    name="Cam H1",
                    latitude=6.9,
                    longitude=79.8,
                    road_segment="SEG-1",
                ),
                CameraRegistry(
                    camera_id="cam_h2",
                    name="Cam H2",
                    latitude=6.91,
                    longitude=79.81,
                    road_segment="SEG-2",
                ),
            ]
        )
        db.add_all(
            [
                TrafficMetric(
                    camera_id="cam_h1",
                    window_start=now - timedelta(minutes=1),
                    window_end=now,
                    lane_id=None,
                    vehicle_count=20,
                    counts_by_class="{}",
                    avg_speed_kmh=15,
                    stopped_ratio=0.1,
                    queue_length=3,
                    congestion_level="MODERATE",
                    congestion_score=0.6,
                ),
                TrafficMetric(
                    camera_id="cam_h2",
                    window_start=now - timedelta(minutes=1),
                    window_end=now,
                    lane_id=None,
                    vehicle_count=10,
                    counts_by_class="{}",
                    avg_speed_kmh=25,
                    stopped_ratio=0.0,
                    queue_length=1,
                    congestion_level="LOW",
                    congestion_score=0.2,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    res = client.get("/api/map/heatmap?minutes=5")
    assert res.status_code == 200
    points = res.json()["points"]
    assert len(points) == 2
    assert all("latitude" in p and "longitude" in p and "weight" in p for p in points)


def test_map_endpoints_skip_cameras_without_coordinates():
    _clear()
    now = datetime.now(UTC).replace(microsecond=0)
    db = _TestingSessionLocal()
    try:
        db.add_all(
            [
                CameraRegistry(
                    camera_id="cam_ok",
                    name="Cam OK",
                    latitude=6.91,
                    longitude=79.85,
                ),
                CameraRegistry(
                    camera_id="cam_missing",
                    name="Cam Missing",
                    latitude=None,
                    longitude=None,
                ),
            ]
        )
        db.add_all(
            [
                TrafficMetric(
                    camera_id="cam_ok",
                    window_start=now - timedelta(minutes=1),
                    window_end=now,
                    lane_id=None,
                    vehicle_count=30,
                    counts_by_class="{}",
                    avg_speed_kmh=8,
                    stopped_ratio=0.4,
                    queue_length=8,
                    congestion_level="SEVERE",
                    congestion_score=0.91,
                ),
                TrafficMetric(
                    camera_id="cam_missing",
                    window_start=now - timedelta(minutes=1),
                    window_end=now,
                    lane_id=None,
                    vehicle_count=28,
                    counts_by_class="{}",
                    avg_speed_kmh=10,
                    stopped_ratio=0.3,
                    queue_length=6,
                    congestion_level="SEVERE",
                    congestion_score=0.89,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    heatmap = client.get("/api/map/heatmap?minutes=5")
    assert heatmap.status_code == 200
    heatmap_cams = [p["camera_id"] for p in heatmap.json()["points"]]
    assert heatmap_cams == ["cam_ok"]

    incidents = client.get("/api/map/incidents")
    assert incidents.status_code == 200
    incident_cams = [p["camera_id"] for p in incidents.json()]
    assert incident_cams == ["cam_ok"]
