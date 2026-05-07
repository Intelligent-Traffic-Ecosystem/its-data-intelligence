import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import AlertAcknowledgement, TrafficMetric
from shared.schemas import (
    AlertAcknowledgeRequest,
    AlertAcknowledgeResponse,
    AlertOutput,
)

router = APIRouter(prefix="/api/alerts")


def _alert_id(row: TrafficMetric) -> str:
    lane = "all" if row.lane_id is None else str(row.lane_id)
    return f"metric-{row.id}-{row.camera_id}-{lane}"


def _metric_to_alert(
    row: TrafficMetric,
    ack: AlertAcknowledgement | None = None,
) -> AlertOutput | None:
    level = row.congestion_level or "LOW"
    score = row.congestion_score or 0.0

    if level not in {"HIGH", "CRITICAL", "EMERGENCY"} and score < 0.7:
        return None

    if level == "EMERGENCY" or score >= 0.95:
        severity = "EMERGENCY"
    elif level == "CRITICAL" or score >= 0.9:
        severity = "CRITICAL"
    else:
        severity = "HIGH"

    return AlertOutput(
        alert_id=_alert_id(row),
        camera_id=row.camera_id,
        road_segment=row.camera_id,
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
        acknowledged=ack is not None,
        acknowledged_by=ack.admin_id if ack else None,
        acknowledged_at=ack.acknowledged_at if ack else None,
    )


def _ack_map(db: Session, alert_ids: list[str]) -> dict[str, AlertAcknowledgement]:
    if not alert_ids:
        return {}
    rows = (
        db.execute(select(AlertAcknowledgement).where(AlertAcknowledgement.alert_id.in_(alert_ids)))
        .scalars()
        .all()
    )
    return {row.alert_id: row for row in rows}


def _query_alert_metrics(
    db: Session,
    start: datetime | None = None,
    end: datetime | None = None,
    severity: str | None = None,
    road_segment: str | None = None,
    alert_type: str | None = None,
):
    stmt = select(TrafficMetric).where(TrafficMetric.lane_id.is_(None))

    if start:
        stmt = stmt.where(TrafficMetric.window_start >= start)
    if end:
        stmt = stmt.where(TrafficMetric.window_start <= end)
    if road_segment:
        stmt = stmt.where(TrafficMetric.camera_id == road_segment)

    rows = db.execute(stmt.order_by(TrafficMetric.window_start.desc())).scalars().all()
    ids = [_alert_id(row) for row in rows]
    acks = _ack_map(db, ids)

    alerts = [
        alert
        for row in rows
        if (alert := _metric_to_alert(row, acks.get(_alert_id(row)))) is not None
    ]

    if severity:
        alerts = [alert for alert in alerts if alert.severity == severity.upper()]
    if alert_type:
        alerts = [alert for alert in alerts if alert.alert_type == alert_type.upper()]

    return alerts


@router.get("/current", response_model=list[AlertOutput])
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

    rows = (
        db.execute(
            select(TrafficMetric)
            .join(
                latest,
                (TrafficMetric.camera_id == latest.c.camera_id)
                & (TrafficMetric.window_start == latest.c.max_ws),
            )
            .where(TrafficMetric.lane_id.is_(None))
        )
        .scalars()
        .all()
    )

    ids = [_alert_id(row) for row in rows]
    acks = _ack_map(db, ids)

    return [
        alert
        for row in rows
        if (alert := _metric_to_alert(row, acks.get(_alert_id(row)))) is not None
    ]


@router.get("/history", response_model=list[AlertOutput])
def get_alert_history(
    severity: str | None = Query(None),
    road_segment: str | None = Query(None),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    alert_type: str | None = Query(None, alias="type"),
    db: Session = Depends(get_db),
):
    return _query_alert_metrics(db, start, end, severity, road_segment, alert_type)


@router.post("/{alert_id}/acknowledge", response_model=AlertAcknowledgeResponse)
def acknowledge_alert(
    alert_id: str,
    payload: AlertAcknowledgeRequest,
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(AlertAcknowledgement).where(AlertAcknowledgement.alert_id == alert_id)
    ).scalar_one_or_none()

    if existing:
        return AlertAcknowledgeResponse(
            alert_id=existing.alert_id,
            admin_id=existing.admin_id,
            acknowledged_at=existing.acknowledged_at,
            status="already_acknowledged",
        )

    ack = AlertAcknowledgement(
        alert_id=alert_id,
        admin_id=payload.admin_id,
        acknowledged_at=datetime.now(UTC),
    )
    db.add(ack)
    db.commit()
    db.refresh(ack)

    return AlertAcknowledgeResponse(
        alert_id=ack.alert_id,
        admin_id=ack.admin_id,
        acknowledged_at=ack.acknowledged_at,
        status="acknowledged",
    )


@router.get("/export")
def export_alert_history(
    severity: str | None = Query(None),
    road_segment: str | None = Query(None),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    alert_type: str | None = Query(None, alias="type"),
    db: Session = Depends(get_db),
):
    alerts = _query_alert_metrics(db, start, end, severity, road_segment, alert_type)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "alert_id",
            "camera_id",
            "road_segment",
            "lane_id",
            "alert_type",
            "severity",
            "message",
            "window_start",
            "window_end",
            "congestion_level",
            "congestion_score",
            "vehicle_count",
            "avg_speed_kmh",
            "queue_length",
            "acknowledged",
            "acknowledged_by",
            "acknowledged_at",
        ]
    )

    for alert in alerts:
        writer.writerow(
            [
                alert.alert_id,
                alert.camera_id,
                alert.road_segment,
                alert.lane_id,
                alert.alert_type,
                alert.severity,
                alert.message,
                alert.window_start.isoformat(),
                alert.window_end.isoformat(),
                alert.congestion_level,
                alert.congestion_score,
                alert.vehicle_count,
                alert.avg_speed_kmh,
                alert.queue_length,
                alert.acknowledged,
                alert.acknowledged_by,
                alert.acknowledged_at.isoformat() if alert.acknowledged_at else "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alert_history.csv"},
    )