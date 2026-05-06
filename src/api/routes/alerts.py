"""Real-time alerting endpoints for B3.

Severity taxonomy (decided unilaterally — flag with B3 in PR review):
  - WARNING   <- congestion_level=HIGH (sustained)
  - CRITICAL  <- congestion_level=SEVERE
  - EMERGENCY <- reserved for explicit incidents (e.g. crash) — manual or
                 future B1 incident events; not auto-generated yet.

Filtering: history accepts severity, road_segment, alert_type, from/to.
Acknowledgement is admin-authenticated; logs to audit_logs per FR.
"""

import csv
import io
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import AdminActor, log_audit, require_admin
from shared.db import get_db
from shared.models import Alert
from shared.schemas import (
    ALERT_SEVERITIES,
    ALERT_TYPES,
    AlertAcknowledgeResponse,
    AlertOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _alert_to_out(row: Alert) -> AlertOut:
    return AlertOut(
        id=row.id,
        severity=row.severity,
        alert_type=row.alert_type,
        camera_id=row.camera_id,
        road_segment=row.road_segment,
        title=row.title,
        message=row.message,
        congestion_level=row.congestion_level,
        congestion_score=row.congestion_score,
        triggered_at=row.triggered_at,
        resolved_at=row.resolved_at,
        acknowledged_by=row.acknowledged_by,
        acknowledged_at=row.acknowledged_at,
    )


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AlertAcknowledgeResponse,
)
def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    if alert.severity not in ("CRITICAL", "EMERGENCY"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CRITICAL or EMERGENCY alerts require acknowledgement",
        )

    if alert.acknowledged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Alert already acknowledged by {alert.acknowledged_by} "
                f"at {alert.acknowledged_at.isoformat()}"
            ),
        )

    now = datetime.utcnow()
    alert.acknowledged_by = actor.actor_id
    alert.acknowledged_at = now
    db.commit()
    db.refresh(alert)

    log_audit(
        db,
        actor.actor_id,
        "alerts.acknowledge",
        "alerts",
        str(alert.id),
        {"severity": alert.severity, "alert_type": alert.alert_type},
    )

    return AlertAcknowledgeResponse(
        id=alert.id,
        acknowledged_by=alert.acknowledged_by,
        acknowledged_at=alert.acknowledged_at,
    )


def _build_history_query(
    severity: str | None,
    road_segment: str | None,
    alert_type: str | None,
    camera_id: str | None,
    start: datetime | None,
    end: datetime | None,
):
    stmt = select(Alert).order_by(Alert.triggered_at.desc())
    if severity:
        if severity not in ALERT_SEVERITIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"severity must be one of {ALERT_SEVERITIES}",
            )
        stmt = stmt.where(Alert.severity == severity)
    if road_segment:
        stmt = stmt.where(Alert.road_segment == road_segment)
    if alert_type:
        if alert_type not in ALERT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"alert_type must be one of {ALERT_TYPES}",
            )
        stmt = stmt.where(Alert.alert_type == alert_type)
    if camera_id:
        stmt = stmt.where(Alert.camera_id == camera_id)
    if start:
        stmt = stmt.where(Alert.triggered_at >= start)
    if end:
        stmt = stmt.where(Alert.triggered_at <= end)
    return stmt


@router.get("/history", response_model=list[AlertOut])
def alert_history(
    severity: str | None = Query(None, description=f"One of {ALERT_SEVERITIES}"),
    road_segment: str | None = Query(None, description="Filter by road segment"),
    alert_type: str | None = Query(None, description=f"One of {ALERT_TYPES}"),
    camera_id: str | None = Query(None),
    start: datetime | None = Query(None, alias="from", description="ISO 8601"),
    end: datetime | None = Query(None, alias="to", description="ISO 8601"),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    stmt = _build_history_query(severity, road_segment, alert_type, camera_id, start, end).limit(
        limit
    )
    rows = db.execute(stmt).scalars().all()
    return [_alert_to_out(r) for r in rows]


def _stream_csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "severity",
            "alert_type",
            "camera_id",
            "road_segment",
            "title",
            "message",
            "congestion_level",
            "congestion_score",
            "triggered_at",
            "resolved_at",
            "acknowledged_by",
            "acknowledged_at",
        ]
    )
    yield buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.severity,
                row.alert_type,
                row.camera_id or "",
                row.road_segment or "",
                row.title,
                row.message or "",
                row.congestion_level or "",
                row.congestion_score if row.congestion_score is not None else "",
                row.triggered_at.isoformat() if row.triggered_at else "",
                row.resolved_at.isoformat() if row.resolved_at else "",
                row.acknowledged_by or "",
                row.acknowledged_at.isoformat() if row.acknowledged_at else "",
            ]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)


@router.get("/export")
def export_alerts(
    severity: str | None = Query(None),
    road_segment: str | None = Query(None),
    alert_type: str | None = Query(None),
    camera_id: str | None = Query(None),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    limit: int = Query(50_000, ge=1, le=500_000),
    db: Session = Depends(get_db),
):
    stmt = _build_history_query(severity, road_segment, alert_type, camera_id, start, end).limit(
        limit
    )
    rows = db.execute(stmt).scalars().all()
    filename = f"alerts_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        _stream_csv(rows),
        media_type="text/csv",
        headers=headers,
    )


# --- Alert generation helper, used by the processor writer ---


def _ensure_camera_has_segment(db: Session, camera_id: str) -> str | None:
    """Look up the road_segment recorded for a camera, if any."""
    from shared.models import Camera  # local import to avoid cycles at startup

    cam = db.execute(select(Camera).where(Camera.camera_id == camera_id)).scalar_one_or_none()
    return cam.road_segment if cam else None


def reconcile_alert_for_metric(db: Session, metric: dict) -> Alert | None:
    """Open / upgrade / close alerts based on a freshly-written metric.

    Strategy:
      - SEVERE  -> ensure open CRITICAL congestion alert
      - HIGH    -> ensure open WARNING  congestion alert (downgrade if previously CRITICAL)
      - LOW/MODERATE -> resolve any open congestion alert for this camera

    Idempotent: only writes when transition state changes.
    """
    camera_id = metric.get("camera_id")
    level = (metric.get("congestion_level") or "").upper()
    if not camera_id:
        return None

    target_severity = None
    if level == "SEVERE":
        target_severity = "CRITICAL"
    elif level == "HIGH":
        target_severity = "WARNING"

    # Load any open congestion alert for this camera
    open_stmt = (
        select(Alert)
        .where(
            Alert.camera_id == camera_id,
            Alert.alert_type == "congestion",
            Alert.resolved_at.is_(None),
        )
        .order_by(Alert.triggered_at.desc())
        .limit(1)
    )
    open_alert = db.execute(open_stmt).scalar_one_or_none()

    if target_severity is None:
        # Resolve any open alert
        if open_alert is not None:
            open_alert.resolved_at = datetime.utcnow()
            db.commit()
            return open_alert
        return None

    road_segment = _ensure_camera_has_segment(db, camera_id)

    if open_alert is None:
        new = Alert(
            severity=target_severity,
            alert_type="congestion",
            camera_id=camera_id,
            road_segment=road_segment,
            title=f"{target_severity}: congestion {level} on {camera_id}",
            message=(
                f"Camera {camera_id} congestion={level} "
                f"score={metric.get('congestion_score'):.2f}"
            ),
            congestion_level=level,
            congestion_score=metric.get("congestion_score"),
            payload=json.dumps(
                {
                    "window_start": str(metric.get("window_start")),
                    "window_end": str(metric.get("window_end")),
                    "vehicle_count": metric.get("vehicle_count"),
                }
            ),
        )
        db.add(new)
        db.commit()
        db.refresh(new)
        return new

    if open_alert.severity != target_severity:
        open_alert.severity = target_severity
        open_alert.congestion_level = level
        open_alert.congestion_score = metric.get("congestion_score")
        db.commit()
        db.refresh(open_alert)
    return open_alert
