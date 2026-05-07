import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficMetric
from shared.schemas import AlertOutput

router = APIRouter()


def _metric_to_alert(row: TrafficMetric) -> AlertOutput | None:
    level = row.congestion_level or "LOW"
    score = row.congestion_score or 0.0

    if level not in {"HIGH", "CRITICAL"} and score < 0.7:
        return None

    severity = "CRITICAL" if level == "CRITICAL" or score >= 0.9 else "HIGH"

    return AlertOutput(
        camera_id=row.camera_id,
        lane_id=row.lane_id,
        alert_type="CONGESTION",
        severity=severity,
        message=f"{severity} congestion detected at {row.camera_id}",
        window_start=row.window_start,
        window_end=row.window_end,
        congestion_level=level,
        congestion_score=score,
        vehicle_count=row.vehicle_count or 0,
        avg_speed_kmh=row.avg_speed_kmh or 0.0,
        queue_length=row.queue_length or 0,
    )


@router.get("/alerts/current", response_model=list[AlertOutput])
def get_current_alerts(db: Session = Depends(get_db)):
    latest = (
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

    rows = db.execute(stmt).scalars().all()
    return [alert for row in rows if (alert := _metric_to_alert(row)) is not None]


@router.get("/alerts/history", response_model=list[AlertOutput])
def get_alert_history(
    camera_id: str | None = Query(None),
    start: datetime = Query(..., alias="from"),
    end: datetime = Query(..., alias="to"),
    db: Session = Depends(get_db),
):
    stmt = select(TrafficMetric).where(
        TrafficMetric.window_start >= start,
        TrafficMetric.window_start <= end,
        TrafficMetric.lane_id.is_(None),
    )

    if camera_id:
        stmt = stmt.where(TrafficMetric.camera_id == camera_id)

    stmt = stmt.order_by(TrafficMetric.window_start.desc())

    rows = db.execute(stmt).scalars().all()
    return [alert for row in rows if (alert := _metric_to_alert(row)) is not None]