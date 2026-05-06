"""Dashboard endpoints (initial SSR/client load) for B3.

Decisions made (flag with B3 in PR):
  - "Total incidents" = alert count over the last 24h (severity ANY).
  - "Avg speed" = average over the latest window per camera (camera-wide rows).
  - "Overall congestion level / score" = average score over the latest 5 min,
    mapped back to LOW/MODERATE/HIGH/SEVERE using the configured thresholds.
  - "Active alerts" = alerts with resolved_at IS NULL.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.config import settings
from shared.db import get_db
from shared.models import AdminThreshold, Alert, TrafficEvent, TrafficMetric
from shared.schemas import DashboardEvent, DashboardSummary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _level_from_score(db: Session, score: float) -> str:
    row = db.execute(select(AdminThreshold).limit(1)).scalar_one_or_none()
    low = row.congestion_threshold_low if row else settings.congestion_threshold_low
    moderate = row.congestion_threshold_moderate if row else settings.congestion_threshold_moderate
    high = row.congestion_threshold_high if row else settings.congestion_threshold_high

    if score < low:
        return "LOW"
    if score < moderate:
        return "MODERATE"
    if score < high:
        return "HIGH"
    return "SEVERE"


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)
    last_5m = now - timedelta(minutes=5)

    incidents_24h = db.execute(
        select(func.count(Alert.id)).where(Alert.triggered_at >= last_24h)
    ).scalar() or 0

    active_alerts = db.execute(
        select(func.count(Alert.id)).where(Alert.resolved_at.is_(None))
    ).scalar() or 0

    avg_speed = db.execute(
        select(func.coalesce(func.avg(TrafficMetric.avg_speed_kmh), 0.0)).where(
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= last_5m,
        )
    ).scalar() or 0.0

    avg_score = db.execute(
        select(func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0)).where(
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= last_5m,
        )
    ).scalar() or 0.0
    avg_score = float(avg_score)

    return DashboardSummary(
        total_incidents_24h=int(incidents_24h),
        avg_speed_kmh=float(avg_speed),
        overall_congestion_level=_level_from_score(db, avg_score),
        overall_congestion_score=avg_score,
        active_alerts=int(active_alerts),
        last_updated=now,
    )


@router.get("/events", response_model=list[DashboardEvent])
def dashboard_events(
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = (
        select(TrafficEvent)
        .order_by(TrafficEvent.ts.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    return [
        DashboardEvent(
            camera_id=row.camera_id,
            timestamp=row.ts,
            vehicle_class=row.vehicle_class,
            speed_kmh=row.speed_kmh,
            lane_id=row.lane_id,
        )
        for row in rows
    ]
