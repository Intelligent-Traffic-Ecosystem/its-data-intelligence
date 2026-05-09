import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from api.event_bus import bus
from shared.config import settings
from shared.db import SessionLocal
from shared.models import TrafficMetric

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self.operator_active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("ws_connected total=%d", len(self.active))

    def register_operator(self, ws: WebSocket):
        if ws not in self.operator_active:
            self.operator_active.append(ws)
        logger.info("operator_ws_registered total=%d", len(self.operator_active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        if ws in self.operator_active:
            self.operator_active.remove(ws)
        logger.info("ws_disconnected remaining=%d", len(self.active))

    async def broadcast(self, message: dict) -> None:
        if not self.active:
            return

        payload = json.dumps(message)
        disconnected: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast_to_operators(self, message: dict) -> None:
        if not self.operator_active:
            return

        payload = json.dumps(message)
        disconnected: list[WebSocket] = []
        for ws in self.operator_active:
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

manager = ConnectionManager()


def _fetch_latest_metrics(camera_filter: str | None = None) -> list[dict]:
    """Query the latest camera-wide metric per camera (lane_id IS NULL).

    If ``camera_filter`` is set, restrict to that one camera.
    """
    db = SessionLocal()
    try:
        latest = (#camerawde dataonly not lane breakdowns
            select(
                TrafficMetric.camera_id,
                func.max(TrafficMetric.window_start).label("max_ws"),
            )
            .where(TrafficMetric.lane_id.is_(None))
            .group_by(TrafficMetric.camera_id)
            .subquery()
        )

        stmt = (
            select(TrafficMetric)
            .join(
                latest,
                (TrafficMetric.camera_id == latest.c.camera_id)
                & (TrafficMetric.window_start == latest.c.max_ws),
            )
            .where(TrafficMetric.lane_id.is_(None))
        )
        if camera_filter:
            stmt = stmt.where(TrafficMetric.camera_id == camera_filter)

        rows = db.execute(stmt).scalars().all()

        return [
            {
                "camera_id": row.camera_id,
                "window_start": row.window_start.isoformat(),
                "window_end": row.window_end.isoformat(),
                "lane_id": row.lane_id,
                "vehicle_count": row.vehicle_count or 0,
                "counts_by_class": json.loads(row.counts_by_class) if row.counts_by_class else {},
                "avg_speed_kmh": row.avg_speed_kmh or 0.0,
                "stopped_ratio": row.stopped_ratio or 0.0,
                "queue_length": row.queue_length or 0,
                "congestion_level": row.congestion_level or "LOW",
                "congestion_score": row.congestion_score or 0.0,
            }
            for row in rows
        ]
    finally:
        db.close()


def _fetch_latest_lane_metrics(camera_filter: str | None = None) -> list[dict]:
    """Query the latest per-lane metric per camera/lane (lane_id IS NOT NULL).

    If ``camera_filter`` is set, restrict to that one camera.
    """
    db = SessionLocal()
    try:
        latest = (
            select(
                TrafficMetric.camera_id,
                TrafficMetric.lane_id,
                func.max(TrafficMetric.window_start).label("max_ws"),
            )
            .where(TrafficMetric.lane_id.is_not(None))
            .group_by(TrafficMetric.camera_id, TrafficMetric.lane_id)
            .subquery()
        )

        stmt = (
            select(TrafficMetric)
            .join(
                latest,
                (TrafficMetric.camera_id == latest.c.camera_id)
                & (TrafficMetric.lane_id == latest.c.lane_id)
                & (TrafficMetric.window_start == latest.c.max_ws),
            )
            .where(TrafficMetric.lane_id.is_not(None))
        )
        if camera_filter:
            stmt = stmt.where(TrafficMetric.camera_id == camera_filter)

        rows = db.execute(stmt).scalars().all()

        return [
            {
                "camera_id": row.camera_id,
                "window_start": row.window_start.isoformat(),
                "window_end": row.window_end.isoformat(),
                "lane_id": row.lane_id,
                "vehicle_count": row.vehicle_count or 0,
                "counts_by_class": json.loads(row.counts_by_class) if row.counts_by_class else {},
                "avg_speed_kmh": row.avg_speed_kmh or 0.0,
                "stopped_ratio": row.stopped_ratio or 0.0,
                "queue_length": row.queue_length or 0,
                "congestion_level": row.congestion_level or "LOW",
                "congestion_score": row.congestion_score or 0.0,
            }
            for row in rows
        ]
    finally:
        db.close()


@router.websocket("/ws/metrics")
async def websocket_metrics(
    ws: WebSocket,
    camera_id: str | None = Query(None),
    role: str = Query(default="viewer", pattern="^(viewer|operator)$"),
):
    """Push the latest camera-wide metric every ws_broadcast_interval seconds.

    Pass ?camera_id=cam_xx to receive only one camera's stream.
    """
    await manager.connect(ws)
    if role == "operator":
        manager.register_operator(ws)
    try:
        while True:
            metrics = _fetch_latest_metrics(camera_filter=camera_id)
            if metrics:
                await ws.send_text(json.dumps(metrics))
            await asyncio.sleep(settings.ws_broadcast_interval)
    except WebSocketDisconnect:
        manager.disconnect(ws)


@router.websocket("/ws/metrics/lanes")
async def websocket_lane_metrics(
    ws: WebSocket,
    camera_id: str | None = Query(None),
    role: str = Query(default="viewer", pattern="^(viewer|operator)$"),
):
    """Push the latest per-lane metric every ws_broadcast_interval seconds.

    Pass ?camera_id=cam_xx to receive only one camera's stream.
    """
    await manager.connect(ws)
    if role == "operator":
        manager.register_operator(ws)
    try:
        while True:
            metrics = _fetch_latest_lane_metrics(camera_filter=camera_id)
            if metrics:
                await ws.send_text(json.dumps(metrics))
            await asyncio.sleep(settings.ws_broadcast_interval)
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Unified real-time event channel (issue #35)
# ---------------------------------------------------------------------------

ALERT_LEVELS = {"HIGH", "SEVERE", "CRITICAL", "EMERGENCY"}


def _heatmap_snapshot(minutes: int = 5) -> list[dict]:
    """Per-camera congestion intensity over the last ``minutes`` minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    db = SessionLocal()
    try:
        stmt = (
            select(
                TrafficMetric.camera_id,
                func.avg(TrafficMetric.congestion_score).label("score"),
                func.avg(TrafficMetric.vehicle_count).label("avg_count"),
            )
            .where(
                TrafficMetric.lane_id.is_(None),
                TrafficMetric.window_start >= cutoff,
            )
            .group_by(TrafficMetric.camera_id)
        )
        return [
            {
                "camera_id": r.camera_id,
                "intensity": round(float(r.score or 0.0), 4),
                "avg_vehicle_count": round(float(r.avg_count or 0.0), 2),
            }
            for r in db.execute(stmt).all()
        ]
    finally:
        db.close()


def _scan_new_alerts(seen: set[tuple]) -> list[dict]:
    """Return alert payloads for cameras whose latest metric flipped to HIGH+.

    ``seen`` carries (camera_id, window_start) pairs we've already alerted on
    so we never re-emit the same alert for the same window.
    """
    metrics = _fetch_latest_metrics()
    fresh: list[dict] = []
    for m in metrics:
        if m["congestion_level"] not in ALERT_LEVELS:
            continue
        key = (m["camera_id"], m["window_start"])
        if key in seen:
            continue
        seen.add(key)
        fresh.append(
            {
                "camera_id": m["camera_id"],
                "severity": m["congestion_level"],
                "score": m["congestion_score"],
                "vehicle_count": m["vehicle_count"],
                "window_end": m["window_end"],
            }
        )
    return fresh


async def _metrics_producer() -> None:
    """Push traffic_metrics_update every 5s (per #35 spec)."""
    while True:
        try:
            data = _fetch_latest_metrics()
            if data:
                bus.publish("traffic_metrics_update", data)
        except Exception:
            logger.exception("metrics_producer_failed")
        await asyncio.sleep(5)


async def _heatmap_producer() -> None:
    """Push heatmap_update every 10s (per #35 spec)."""
    while True:
        try:
            points = _heatmap_snapshot()
            if points:
                bus.publish("heatmap_update", points)
        except Exception:
            logger.exception("heatmap_producer_failed")
        await asyncio.sleep(10)


async def _alerts_producer() -> None:
    """Detect new HIGH/SEVERE windows and push new_alert events.

    Polls at 2s; alerts are emitted at most once per (camera, window).
    """
    seen: set[tuple] = set()
    while True:
        try:
            for alert in _scan_new_alerts(seen):
                bus.publish("new_alert", alert)
        except Exception:
            logger.exception("alerts_producer_failed")
        # Cap memory: keep only last 5k entries (~7 hours at 5s windows / cam).
        if len(seen) > 5000:
            seen = set(list(seen)[-2500:])
        await asyncio.sleep(2)


_producer_tasks: list[asyncio.Task] = []


def start_event_producers() -> None:
    """Spawn the background producer tasks. Idempotent."""
    if _producer_tasks:
        return
    loop = asyncio.get_event_loop()
    _producer_tasks.append(loop.create_task(_metrics_producer()))
    _producer_tasks.append(loop.create_task(_heatmap_producer()))
    _producer_tasks.append(loop.create_task(_alerts_producer()))
    logger.info("event_producers_started count=%d", len(_producer_tasks))


@router.websocket("/ws/events")
async def websocket_events(
    ws: WebSocket,
    types: str | None = Query(
        None,
        description="Comma-separated event types to subscribe to. "
        "Defaults to all: traffic_metrics_update,heatmap_update,new_alert,admin_broadcast",
    ),
):
    """Unified real-time event channel (issue #35).

    Streams JSON envelopes ``{event, ts, data}`` for:

    - ``traffic_metrics_update`` — every 5s, latest camera-wide metrics.
    - ``heatmap_update`` — every 10s, rolling 5-min congestion heatmap.
    - ``new_alert`` — instant push when a camera flips to HIGH/SEVERE.
    - ``admin_broadcast`` — pushed by the admin notification endpoint.
    """
    await ws.accept()

    requested: set[str] | None = None
    if types:
        requested = {t.strip() for t in types.split(",") if t.strip()}

    queue = bus.subscribe()
    try:
        while True:
            envelope = await queue.get()
            if requested and envelope["event"] not in requested:
                continue
            await ws.send_text(json.dumps(envelope))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_events_send_failed")
    finally:
        bus.unsubscribe(queue)
