"""Smoke tests for B3-facing endpoints.

Uses an in-memory SQLite engine + dependency override so tests do not require
a running Postgres. Covers auth gating, basic happy paths, and CSV/PDF
content-types. Heavy aggregation correctness is exercised by the integration
suite — these tests just guarantee the routes wire up.
"""

import os
from datetime import datetime, timedelta, timezone

# Configure DB env BEFORE importing app modules
os.environ.setdefault("POSTGRES_URL", "sqlite:///:memory:")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from shared.db import get_db
from shared.models import Alert, Base, Camera, TrafficEvent, TrafficMetric

TEST_TOKEN = "test-admin-key"
HEADERS = {"X-Admin-Token": TEST_TOKEN, "X-Admin-User": "tester"}


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield SessionLocal
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


@pytest.fixture
def client(db_session):
    return TestClient(app)


# --- Alerts ---

def test_acknowledge_requires_admin(client):
    r = client.post("/api/alerts/1/acknowledge")
    assert r.status_code == 401


def test_acknowledge_404(client):
    r = client.post("/api/alerts/999/acknowledge", headers=HEADERS)
    assert r.status_code == 404


def test_acknowledge_critical_alert(client, db_session):
    s = db_session()
    s.add(
        Alert(
            severity="CRITICAL",
            alert_type="congestion",
            camera_id="cam-1",
            title="test",
        )
    )
    s.commit()
    aid = s.query(Alert).one().id
    s.close()

    r = client.post(f"/api/alerts/{aid}/acknowledge", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["acknowledged_by"] == "tester"
    assert body["id"] == aid


def test_acknowledge_warning_rejected(client, db_session):
    s = db_session()
    s.add(Alert(severity="WARNING", alert_type="congestion", camera_id="cam-1", title="t"))
    s.commit()
    aid = s.query(Alert).one().id
    s.close()

    r = client.post(f"/api/alerts/{aid}/acknowledge", headers=HEADERS)
    assert r.status_code == 400


def test_acknowledge_idempotent(client, db_session):
    s = db_session()
    s.add(
        Alert(
            severity="CRITICAL",
            alert_type="congestion",
            camera_id="cam-1",
            title="t",
            acknowledged_by="someone",
            acknowledged_at=datetime.utcnow(),
        )
    )
    s.commit()
    aid = s.query(Alert).one().id
    s.close()

    r = client.post(f"/api/alerts/{aid}/acknowledge", headers=HEADERS)
    assert r.status_code == 409


def test_alert_history_filters(client, db_session):
    s = db_session()
    now = datetime.utcnow()
    s.add_all(
        [
            Alert(
                severity="WARNING",
                alert_type="congestion",
                camera_id="cam-A",
                road_segment="seg-1",
                title="w1",
                triggered_at=now - timedelta(hours=1),
            ),
            Alert(
                severity="CRITICAL",
                alert_type="congestion",
                camera_id="cam-B",
                road_segment="seg-2",
                title="c1",
                triggered_at=now,
            ),
        ]
    )
    s.commit()
    s.close()

    r = client.get("/api/alerts/history")
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get("/api/alerts/history", params={"severity": "CRITICAL"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["severity"] == "CRITICAL"

    r = client.get("/api/alerts/history", params={"road_segment": "seg-1"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["camera_id"] == "cam-A"


def test_alert_history_invalid_severity(client):
    r = client.get("/api/alerts/history", params={"severity": "BAD"})
    assert r.status_code == 400


def test_alert_export_csv(client, db_session):
    s = db_session()
    s.add(Alert(severity="WARNING", alert_type="congestion", camera_id="x", title="t"))
    s.commit()
    s.close()

    r = client.get("/api/alerts/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text
    assert "severity" in body.splitlines()[0]
    assert "WARNING" in body


# --- Dashboard ---

def test_dashboard_summary_empty(client):
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_incidents_24h"] == 0
    assert body["active_alerts"] == 0
    assert body["overall_congestion_level"] in ("LOW", "MODERATE", "HIGH", "SEVERE")


def test_dashboard_events_empty(client):
    r = client.get("/api/dashboard/events")
    assert r.status_code == 200
    assert r.json() == []


def test_dashboard_events_returns_recent(client, db_session):
    s = db_session()
    now = datetime.now(timezone.utc)
    for i in range(15):
        s.add(
            TrafficEvent(
                camera_id=f"cam-{i % 3}",
                ts=now - timedelta(seconds=i),
                vehicle_class="car",
                speed_kmh=40.0,
            )
        )
    s.commit()
    s.close()

    r = client.get("/api/dashboard/events", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 5


# --- Map ---

def test_map_heatmap_skips_cameras_without_geo(client, db_session):
    s = db_session()
    s.add(Camera(camera_id="cam-noloc"))  # no lat/lng
    s.add(Camera(camera_id="cam-loc", latitude=6.9, longitude=79.86))
    now = datetime.now(timezone.utc)
    s.add_all(
        [
            TrafficMetric(
                camera_id="cam-noloc",
                window_start=now,
                window_end=now + timedelta(seconds=5),
                vehicle_count=10,
                congestion_score=0.4,
                congestion_level="MODERATE",
            ),
            TrafficMetric(
                camera_id="cam-loc",
                window_start=now,
                window_end=now + timedelta(seconds=5),
                vehicle_count=20,
                congestion_score=0.6,
                congestion_level="HIGH",
            ),
        ]
    )
    s.commit()
    s.close()

    r = client.get("/api/map/heatmap")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["camera_id"] == "cam-loc"
    assert 0.0 <= body[0]["weight"] <= 1.0


def test_map_incidents_lists_open_alerts(client, db_session):
    s = db_session()
    s.add(Camera(camera_id="cam-1", latitude=6.9, longitude=79.86))
    s.add_all(
        [
            Alert(severity="CRITICAL", alert_type="congestion", camera_id="cam-1", title="open"),
            Alert(
                severity="WARNING",
                alert_type="congestion",
                camera_id="cam-1",
                title="resolved",
                resolved_at=datetime.utcnow(),
            ),
        ]
    )
    s.commit()
    s.close()

    r = client.get("/api/map/incidents")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["title"] == "open"
    assert body[0]["latitude"] == 6.9


# --- Analytics ---

def test_analytics_metrics_validates_range(client):
    now = datetime.utcnow()
    r = client.get(
        "/api/analytics/metrics",
        params={"from": now.isoformat(), "to": (now - timedelta(hours=1)).isoformat()},
    )
    assert r.status_code == 400


def test_analytics_metrics_empty(client):
    now = datetime.utcnow()
    r = client.get(
        "/api/analytics/metrics",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["avg_congestion_score"] == 0.0
    assert body["top_segments"] == []


def test_analytics_compare(client):
    now = datetime.utcnow()
    r = client.get(
        "/api/analytics/compare",
        params={
            "aFrom": (now - timedelta(days=2)).isoformat(),
            "aTo": (now - timedelta(days=1)).isoformat(),
            "bFrom": (now - timedelta(days=1)).isoformat(),
            "bTo": now.isoformat(),
        },
    )
    assert r.status_code == 200
    assert "range_a" in r.json()
    assert "range_b" in r.json()


def test_analytics_pdf(client):
    pytest.importorskip("reportlab")
    now = datetime.utcnow()
    r = client.get(
        "/api/analytics/report/pdf",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


# --- Admin cameras ---

def test_admin_cameras_requires_auth(client):
    r = client.get("/api/admin/cameras")
    assert r.status_code == 401


def test_admin_cameras_crud(client, db_session):
    payload = {
        "camera_id": "cam-X",
        "name": "Galle Rd #1",
        "latitude": 6.9,
        "longitude": 79.86,
        "road_segment": "Galle Rd",
    }
    r = client.post("/api/admin/cameras", json=payload, headers=HEADERS)
    assert r.status_code == 201, r.text

    r = client.get("/api/admin/cameras", headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.put(
        "/api/admin/cameras/cam-X",
        json={"latitude": 7.0, "longitude": 80.0},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["latitude"] == 7.0

    r = client.delete("/api/admin/cameras/cam-X", headers=HEADERS)
    assert r.status_code == 204
