"""Map data endpoints (Mapbox heatmap + incident markers) for B3.

Decisions made (flag with B3 in PR):
  - Heatmap source = camera-wide rows from the last 5 minutes, joined to
    `cameras` table for lat/lng. Cameras without coordinates are omitted.
  - "weight" is normalised vehicle_count / max_vehicle_count from settings,
    clamped to [0, 1]. Suitable for a Mapbox heatmap-weight expression.
  - "Active incident markers" = unresolved alerts joined to camera coords.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import settings
from shared.db import get_db
from shared.models import Alert, Camera, TrafficMetric
from shared.schemas import HeatmapPoint, IncidentMarker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/map", tags=["map"])


@router.get("/heatmap", response_model=list[HeatmapPoint])
def map_heatmap(db: Session = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)

    # Latest camera-wide row per camera within the last 5 minutes
    latest_stmt = (
        select(
            TrafficMetric.camera_id,
            TrafficMetric.vehicle_count,
            TrafficMetric.window_start,
        )
        .where(
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= cutoff,
        )
        .order_by(TrafficMetric.camera_id, TrafficMetric.window_start.desc())
    )
    seen: dict[str, int] = {}
    for cam_id, count, _ws in db.execute(latest_stmt).all():
        if cam_id in seen:
            continue
        seen[cam_id] = count or 0

    if not seen:
        return []

    cam_rows = db.execute(
        select(Camera).where(Camera.camera_id.in_(list(seen.keys())))
    ).scalars().all()

    max_count = max(1, settings.max_vehicle_count)
    points: list[HeatmapPoint] = []
    for cam in cam_rows:
        if cam.latitude is None or cam.longitude is None:
            continue
        vc = seen.get(cam.camera_id, 0)
        weight = max(0.0, min(1.0, vc / max_count))
        points.append(
            HeatmapPoint(
                camera_id=cam.camera_id,
                latitude=cam.latitude,
                longitude=cam.longitude,
                weight=weight,
                vehicle_count=vc,
            )
        )
    return points


@router.get("/incidents", response_model=list[IncidentMarker])
def map_incidents(db: Session = Depends(get_db)):
    open_alerts = db.execute(
        select(Alert).where(Alert.resolved_at.is_(None)).order_by(Alert.triggered_at.desc())
    ).scalars().all()

    if not open_alerts:
        return []

    cam_ids = {a.camera_id for a in open_alerts if a.camera_id}
    cam_lookup: dict[str, Camera] = {}
    if cam_ids:
        cams = (
            db.execute(select(Camera).where(Camera.camera_id.in_(list(cam_ids))))
            .scalars()
            .all()
        )
        cam_lookup = {c.camera_id: c for c in cams}

    return [
        IncidentMarker(
            alert_id=a.id,
            camera_id=a.camera_id,
            latitude=cam_lookup.get(a.camera_id).latitude if a.camera_id in cam_lookup else None,
            longitude=cam_lookup.get(a.camera_id).longitude if a.camera_id in cam_lookup else None,
            severity=a.severity,
            alert_type=a.alert_type,
            title=a.title,
            triggered_at=a.triggered_at,
        )
        for a in open_alerts
    ]
