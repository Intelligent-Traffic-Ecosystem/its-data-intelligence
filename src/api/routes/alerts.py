import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import AlertRecord
from shared.schemas import (
    AlertAcknowledgeRequest,
    AlertAcknowledgeResponse,
    AlertOutput,
)

router = APIRouter(prefix="/api/alerts")


def _query_alert_metrics(
    db: Session,
    start: datetime | None = None,
    end: datetime | None = None,
    severity: str | None = None,
    road_segment: str | None = None,
    alert_type: str | None = None,
    camera_id: str | None = None,
):
    stmt = select(AlertRecord)

    if start:
        stmt = stmt.where(AlertRecord.triggered_at >= start)
    if end:
        stmt = stmt.where(AlertRecord.triggered_at <= end)
    if severity:
        stmt = stmt.where(AlertRecord.severity == severity.upper())
    if road_segment:
        stmt = stmt.where(AlertRecord.road_segment == road_segment)
    if alert_type:
        stmt = stmt.where(AlertRecord.alert_type == alert_type.upper())
    if camera_id:
        stmt = stmt.where(AlertRecord.camera_id == camera_id)

    rows = db.execute(stmt.order_by(AlertRecord.triggered_at.desc())).scalars().all()
    return [
        AlertOutput(
            id=row.id,
            camera_id=row.camera_id,
            road_segment=row.road_segment,
            alert_type=row.alert_type,
            severity=row.severity,
            title=row.title,
            message=row.message,
            congestion_level=row.congestion_level,
            congestion_score=row.congestion_score,
            triggered_at=row.triggered_at,
            resolved_at=row.resolved_at,
            acknowledged=row.acknowledged_at is not None,
            acknowledged_by=row.acknowledged_by,
            acknowledged_at=row.acknowledged_at,
        )
        for row in rows
    ]


@router.get("/active", response_model=list[AlertOutput])
def get_active_alerts(db: Session = Depends(get_db)):
    """Return all alerts that have not yet been acknowledged."""
    stmt = (
        select(AlertRecord)
        .where(AlertRecord.acknowledged_at.is_(None))
        .order_by(AlertRecord.triggered_at.desc())
    )
    rows = db.execute(stmt).scalars().all()
    return [
        AlertOutput(
            id=row.id,
            camera_id=row.camera_id,
            road_segment=row.road_segment,
            alert_type=row.alert_type,
            severity=row.severity,
            title=row.title,
            message=row.message,
            congestion_level=row.congestion_level,
            congestion_score=row.congestion_score,
            triggered_at=row.triggered_at,
            resolved_at=row.resolved_at,
            acknowledged=False,
            acknowledged_by=None,
            acknowledged_at=None,
        )
        for row in rows
    ]


@router.get("/history", response_model=list[AlertOutput])
def get_alert_history(
    severity: str | None = Query(None),
    road_segment: str | None = Query(None),
    camera_id: str | None = Query(None),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    alert_type: str | None = Query(None, alias="type"),
    db: Session = Depends(get_db),
):
    return _query_alert_metrics(db, start, end, severity, road_segment, alert_type, camera_id)


@router.post("/{id}/acknowledge", response_model=AlertAcknowledgeResponse)
def acknowledge_alert(
    id: int,
    payload: AlertAcknowledgeRequest,
    db: Session = Depends(get_db),
):
    alert = db.get(AlertRecord, id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    if alert.acknowledged_at is not None:
        return AlertAcknowledgeResponse(
            alert_id=alert.id,
            admin_id=alert.acknowledged_by or payload.admin_id,
            acknowledged_at=alert.acknowledged_at,
            status="already_acknowledged",
        )

    alert.acknowledged_by = payload.admin_id
    alert.acknowledged_at = datetime.now(UTC)
    db.commit()
    db.refresh(alert)

    return AlertAcknowledgeResponse(
        alert_id=alert.id,
        admin_id=alert.acknowledged_by,
        acknowledged_at=alert.acknowledged_at,
        status="acknowledged",
    )


@router.get("/export")
def export_alert_history(
    severity: str | None = Query(None),
    road_segment: str | None = Query(None),
    camera_id: str | None = Query(None),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    alert_type: str | None = Query(None, alias="type"),
    db: Session = Depends(get_db),
):
    alerts = _query_alert_metrics(
        db, start, end, severity, road_segment, alert_type, camera_id
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "alert_id",
            "camera_id",
            "road_segment",
            "alert_type",
            "severity",
            "title",
            "message",
            "triggered_at",
            "resolved_at",
            "congestion_level",
            "congestion_score",
            "acknowledged",
            "acknowledged_by",
            "acknowledged_at",
        ]
    )

    for alert in alerts:
        writer.writerow(
            [
                alert.id,
                alert.camera_id,
                alert.road_segment,
                alert.alert_type,
                alert.severity,
                alert.title,
                alert.message,
                alert.triggered_at.isoformat(),
                alert.resolved_at.isoformat() if alert.resolved_at else "",
                alert.congestion_level,
                alert.congestion_score,
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
