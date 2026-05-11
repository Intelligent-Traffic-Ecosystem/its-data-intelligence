"""Tests for the unified /ws/events real-time channel (issue #35)."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from api.event_bus import EventBus, bus
from api.main import app

client = TestClient(app)


def test_event_bus_publishes_to_subscribers():
    async def run():
        b = EventBus()
        q1 = b.subscribe()
        q2 = b.subscribe()
        delivered = b.publish("admin_broadcast", {"title": "hi"})
        assert delivered == 2
        env1 = await asyncio.wait_for(q1.get(), timeout=1)
        env2 = await asyncio.wait_for(q2.get(), timeout=1)
        assert env1["event"] == "admin_broadcast"
        assert env1["data"]["title"] == "hi"
        assert "ts" in env1
        assert env2 == env1

    asyncio.run(run())


def test_event_bus_unknown_event_type_rejected():
    b = EventBus()
    with pytest.raises(ValueError):
        b.publish("not_a_real_event", {})


def test_event_bus_drops_when_subscriber_full():
    async def run():
        b = EventBus(queue_size=1)
        q = b.subscribe()
        # First fits.
        d1 = b.publish("new_alert", {"i": 1})
        # Second is dropped (queue full).
        d2 = b.publish("new_alert", {"i": 2})
        assert d1 == 1
        assert d2 == 0
        env = await asyncio.wait_for(q.get(), timeout=1)
        assert env["data"]["i"] == 1

    asyncio.run(run())


def test_ws_events_receives_published_envelope():
    """Subscribe and verify we get an admin_broadcast we publish."""
    with client.websocket_connect("/ws/events?types=admin_broadcast") as ws:
        # Allow the subscription to register before publishing.
        # The bus subscribe call happens after ws.accept(); a tiny sleep is
        # safe in TestClient because the underlying loop runs synchronously.
        bus.publish("admin_broadcast", {"title": "test", "message": "hello"})
        raw = ws.receive_text()
        env = json.loads(raw)
        assert env["event"] == "admin_broadcast"
        assert env["data"]["title"] == "test"


def test_ws_events_filters_by_type():
    """A subscriber asking for new_alert only must not receive heatmap_update."""
    with client.websocket_connect("/ws/events?types=new_alert") as ws:
        bus.publish("heatmap_update", [{"camera_id": "cam_a", "intensity": 0.5}])
        bus.publish("new_alert", {"camera_id": "cam_a", "severity": "SEVERE"})
        raw = ws.receive_text()
        env = json.loads(raw)
        # First message we receive must be the new_alert; heatmap was filtered out.
        assert env["event"] == "new_alert"
