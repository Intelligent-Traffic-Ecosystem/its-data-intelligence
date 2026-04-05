import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficMetric
from shared.schemas import TrafficMetricOutput

router = APIRouter()


def _row_to_output(row: TrafficMetric) -> TrafficMetricOutput:
    return TrafficMetricOutput(
        camera_id=row.camera_id,
        window_start=row.window_start,
        window_end=row.window_end,
        vehicle_count=row.vehicle_count or 0,
        counts_by_class=json.loads(row.counts_by_class) if row.counts_by_class else {},
        avg_speed_kmh=row.avg_speed_kmh or 0.0,
        stopped_ratio=row.stopped_ratio or 0.0,
        queue_length=row.queue_length or 0,
        congestion_level=row.congestion_level or "LOW",
        congestion_score=row.congestion_score or 0.0,
    )


@router.get("/metrics/current", response_model=TrafficMetricOutput)
def get_current_metrics(
    camera_id: str = Query(..., description="Camera ID"),
    db: Session = Depends(get_db),
):
    """Get the latest aggregated metrics for a specific camera."""
    stmt = (
        select(TrafficMetric)
        .where(TrafficMetric.camera_id == camera_id)
        .order_by(TrafficMetric.window_start.desc())
        .limit(1)
    )
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        return TrafficMetricOutput(
            camera_id=camera_id,
            window_start=datetime.min,
            window_end=datetime.min,
            vehicle_count=0,
            counts_by_class={},
            avg_speed_kmh=0.0,
            stopped_ratio=0.0,
            queue_length=0,
            congestion_level="LOW",
            congestion_score=0.0,
        )
    return _row_to_output(row)


@router.get("/metrics/history", response_model=list[TrafficMetricOutput])
def get_metrics_history(
    camera_id: str = Query(..., description="Camera ID"),
    start: datetime = Query(..., alias="from", description="Start time (ISO 8601)"),
    end: datetime = Query(..., alias="to", description="End time (ISO 8601)"),
    db: Session = Depends(get_db),
):
    """Get historical metrics for a camera within a time range."""
    stmt = (
        select(TrafficMetric)
        .where(
            TrafficMetric.camera_id == camera_id,
            TrafficMetric.window_start >= start,
            TrafficMetric.window_start <= end,
        )
        .order_by(TrafficMetric.window_start.asc())
    )
    rows = db.execute(stmt).scalars().all()
    return [_row_to_output(row) for row in rows]
