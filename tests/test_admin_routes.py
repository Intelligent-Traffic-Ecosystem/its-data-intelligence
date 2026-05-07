from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
ADMIN_HEADERS = {"X-Admin-Token": "test-admin-key", "X-Admin-User": "tester"}


def _admin_headers(**overrides):
    headers = dict(ADMIN_HEADERS)
    headers.update(overrides)
    return headers


def _threshold_payload(low=0.3, moderate=0.55, high=0.8):
    return {
        "congestion_threshold_low": low,
        "congestion_threshold_moderate": moderate,
        "congestion_threshold_high": high,
    }


def _zone_payload(name="Central Zone", description="Main monitoring zone"):
    return {
        "name": name,
        "description": description,
        "coordinates": [
            {"lat": 6.9271, "lon": 79.8612},
            {"lat": 6.9275, "lon": 79.8630},
            {"lat": 6.9255, "lon": 79.8633},
        ],
    }


def test_admin_thresholds_require_auth():
    from shared.config import settings

    settings.admin_api_key = "test-admin-key"
    response = client.get("/api/admin/thresholds")
    assert response.status_code == 401


def test_admin_thresholds_get_put_and_audit(monkeypatch):
    from shared.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")

    with patch("api.routes.admin._log_audit") as log_audit:
        response = client.get("/api/admin/thresholds", headers=_admin_headers())
        assert response.status_code == 200
        assert response.json() == _threshold_payload()

        update = client.put(
            "/api/admin/thresholds",
            headers=_admin_headers(),
            json=_threshold_payload(0.25, 0.5, 0.9),
        )
        assert update.status_code == 200
        assert update.json() == _threshold_payload(0.25, 0.5, 0.9)
        assert log_audit.call_count == 1


def test_admin_threshold_validation(monkeypatch):
    from shared.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")

    response = client.put(
        "/api/admin/thresholds",
        headers=_admin_headers(),
        json=_threshold_payload(0.6, 0.5, 0.9),
    )
    assert response.status_code == 422


def test_admin_zones_crud_and_audit(monkeypatch):
    from shared.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")

    with patch("api.routes.admin._log_audit") as log_audit:
        created = client.post(
            "/api/admin/zones",
            headers=_admin_headers(),
            json=_zone_payload(),
        )
        assert created.status_code == 201
        zone = created.json()
        assert zone["name"] == "Central Zone"
        assert zone["coordinates"][0] == zone["coordinates"][-1]

        listed = client.get("/api/admin/zones", headers=_admin_headers())
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        updated = client.put(
            f"/api/admin/zones/{zone['id']}",
            headers=_admin_headers(),
            json=_zone_payload(name="Updated Zone", description="Updated"),
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated Zone"

        deleted = client.delete(
            f"/api/admin/zones/{zone['id']}",
            headers=_admin_headers(),
        )
        assert deleted.status_code == 204

        assert log_audit.call_count == 3


def test_admin_zone_validation(monkeypatch):
    from shared.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")

    response = client.post(
        "/api/admin/zones",
        headers=_admin_headers(),
        json={
            "name": "Broken Zone",
            "description": "",
            "coordinates": [
                {"lat": 6.9, "lon": 79.8},
                {"lat": 6.91, "lon": 79.81},
            ],
        },
    )
    assert response.status_code == 422


def test_admin_notifications_broadcast(monkeypatch):
    from shared.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key")

    with patch("api.routes.admin.manager.broadcast_to_operators", new=AsyncMock()) as broadcast, \
         patch("api.routes.admin.manager.operator_active", [object(), object()]), \
         patch("api.routes.admin._log_audit") as log_audit:
        response = client.post(
            "/api/admin/notifications/broadcast",
            headers=_admin_headers(),
            json={"message": "Check zone", "severity": "warning", "title": "Alert"},
        )

    assert response.status_code == 202
    assert response.json() == {"status": "queued", "recipients": 2}
    broadcast.assert_awaited_once()
    log_audit.assert_called_once()