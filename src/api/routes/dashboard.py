from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, distinct
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficEvent, TrafficMetric
from shared.schemas import DashboardSummaryResponse, TrafficEventOutput

router = APIRouter(prefix="/api")


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    start: datetime | None = Query(None, alias="from", description="Start time (ISO 8601). Defaults to 1 hour ago."),
    end: datetime | None = Query(None, alias="to", description="End time (ISO 8601). Defaults to now."),
    db: Session = Depends(get_db),
):
    """Get high-level KPIs for the dashboard across a specific time window."""
    now = datetime.now(timezone.utc)
    if end is None:
        end = now
    if start is None:
        start = end - timedelta(hours=1)

    # 1. Base query for metrics in the time window (camera-wide only)
    base_conds = [
        TrafficMetric.window_start >= start,
        TrafficMetric.window_start <= end,
        TrafficMetric.lane_id.is_(None),
    ]

    # 2. Total vehicles, average speed, and active cameras
    stmt_stats = select(
        func.count(distinct(TrafficMetric.camera_id)),
        func.sum(TrafficMetric.vehicle_count),
        func.avg(TrafficMetric.avg_speed_kmh)
    ).where(*base_conds)
    
    stats_row = db.execute(stmt_stats).first()
    active_cameras = stats_row[0] or 0
    total_vehicles = stats_row[1] or 0
    avg_speed = stats_row[2] or 0.0

    # 3. Severe congestion count (distinct cameras with SEVERE in window)
    stmt_severe = select(func.count(distinct(TrafficMetric.camera_id))).where(
        *base_conds,
        TrafficMetric.congestion_level == "SEVERE"
    )
    severe_count = db.execute(stmt_severe).scalar() or 0

    # 4. Busiest camera
    stmt_busiest = (
        select(TrafficMetric.camera_id)
        .where(*base_conds)
        .group_by(TrafficMetric.camera_id)
        .order_by(func.sum(TrafficMetric.vehicle_count).desc())
        .limit(1)
    )
    busiest_camera_id = db.execute(stmt_busiest).scalar_one_or_none()

    return DashboardSummaryResponse(
        active_cameras=active_cameras,
        total_vehicles=int(total_vehicles),
        average_speed=float(avg_speed),
        severe_congestion_count=severe_count,
        busiest_camera_id=busiest_camera_id,
    )


@router.get("/dashboard/events", response_model=list[TrafficEventOutput])
def get_dashboard_events(
    camera_id: str | None = Query(None, description="Optional camera ID to filter by"),
    limit: int = Query(10, ge=1, le=100, description="Number of events to return"),
    db: Session = Depends(get_db),
):
    """Get the latest traffic events across the system or for a specific camera."""
    stmt = select(TrafficEvent).order_by(TrafficEvent.ts.desc())
    if camera_id:
        stmt = stmt.where(TrafficEvent.camera_id == camera_id)
    stmt = stmt.limit(limit)

    rows = db.execute(stmt).scalars().all()

    return [
        TrafficEventOutput(
            id=row.id,
            camera_id=row.camera_id,
            timestamp=row.ts,
            vehicle_id=row.vehicle_id,
            vehicle_class=row.vehicle_class,
            speed_kmh=row.speed_kmh,
            confidence=row.confidence,
            lane_id=row.lane_id,
            bbox_x=row.bbox_x,
            bbox_y=row.bbox_y,
            bbox_w=row.bbox_w,
            bbox_h=row.bbox_h,
        )
        for row in rows
    ]
