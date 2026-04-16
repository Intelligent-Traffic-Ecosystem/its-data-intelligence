import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from shared.config import settings
from shared.db import SessionLocal
from shared.models import TrafficMetric

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("WebSocket client connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(self.active))

    async def broadcast(self, data: str):
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.active.remove(ws)


manager = ConnectionManager()


def _fetch_latest_metrics() -> list[dict]:
    """Query the latest metric per camera from Postgres."""
    db = SessionLocal()
    try:
        latest = (
            select(
                TrafficMetric.camera_id,
                func.max(TrafficMetric.window_start).label("max_ws"),
            )
            .group_by(TrafficMetric.camera_id)
            .subquery()
        )

        stmt = select(TrafficMetric).join(
            latest,
            (TrafficMetric.camera_id == latest.c.camera_id)
            & (TrafficMetric.window_start == latest.c.max_ws),
        )

        rows = db.execute(stmt).scalars().all()

        return [
            {
                "camera_id": row.camera_id,
                "window_start": row.window_start.isoformat(),
                "window_end": row.window_end.isoformat(),
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
async def websocket_metrics(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            metrics = _fetch_latest_metrics()
            if metrics:
                await ws.send_text(json.dumps(metrics))
            await asyncio.sleep(settings.ws_broadcast_interval)
    except WebSocketDisconnect:
        manager.disconnect(ws)
