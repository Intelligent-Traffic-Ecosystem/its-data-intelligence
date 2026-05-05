import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
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
        logger.info("ws_connected total=%d", len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
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


@router.websocket("/ws/metrics")
async def websocket_metrics(ws: WebSocket, camera_id: str | None = Query(None)):
    """Push the latest camera-wide metric every ws_broadcast_interval seconds.

    Pass ?camera_id=cam_xx to receive only one camera's stream.
    """
    await manager.connect(ws)
    try:
        while True:
            metrics = _fetch_latest_metrics(camera_filter=camera_id)
            if metrics:
                await ws.send_text(json.dumps(metrics))
            await asyncio.sleep(settings.ws_broadcast_interval)
    except WebSocketDisconnect:
        manager.disconnect(ws)
