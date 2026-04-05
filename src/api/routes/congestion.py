import json

from fastapi import APIRouter, Depends
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficMetric
from shared.schemas import TrafficMetricOutput

router = APIRouter()


@router.get("/congestion/current", response_model=list[TrafficMetricOutput])
def get_current_congestion(db: Session = Depends(get_db)):
    """Get the current congestion level for all cameras (latest window per camera)."""
    # Subquery: latest window_start per camera
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
        TrafficMetricOutput(
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
        for row in rows
    ]
