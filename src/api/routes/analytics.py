"""Historical analytics endpoints for B3.

Decisions made (flag with B3 in PR):
  - "Top 10 segments" ranks by avg congestion_score over the range. Tie-broken
    by total severe minutes. Falls back to camera_id when road_segment is null.
  - "Peak hour distribution" buckets by hour-of-day in UTC and reports
    avg vehicle_count and avg congestion_score per bucket.
  - "Incident pie chart" counts alerts per severity over the range.
  - PDF rendering uses ReportLab (no native deps, MIT-friendly).
"""

import io
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, extract, func, select
from sqlalchemy.orm import Session

from shared.db import engine, get_db
from shared.models import Alert, TrafficMetric
from shared.schemas import (
    AnalyticsCompare,
    AnalyticsMetrics,
    IncidentSlice,
    PeakHourBucket,
    TopSegment,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _compute_metrics(db: Session, start: datetime, end: datetime) -> AnalyticsMetrics:
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`to` must be after `from`",
        )

    # Avg congestion (camera-wide rows only — lane_id IS NULL)
    avg_stmt = (
        select(func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0))
        .where(
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= start,
            TrafficMetric.window_start <= end,
        )
    )
    avg_score = float(db.execute(avg_stmt).scalar() or 0.0)

    # Peak hour distribution
    hour_col = extract("hour", TrafficMetric.window_start)
    peak_stmt = (
        select(
            hour_col.label("hour"),
            func.coalesce(func.avg(TrafficMetric.vehicle_count), 0.0),
            func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0),
        )
        .where(
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= start,
            TrafficMetric.window_start <= end,
        )
        .group_by(hour_col)
        .order_by(hour_col)
    )
    peak_rows = db.execute(peak_stmt).all()
    peak_hour_distribution = [
        PeakHourBucket(
            hour=int(r[0] or 0),
            avg_vehicle_count=float(r[1] or 0.0),
            avg_congestion_score=float(r[2] or 0.0),
        )
        for r in peak_rows
    ]

    # Top 10 segments — ranked by avg congestion_score; sum severe-window minutes
    # as the second-order metric (window_size * count where level=SEVERE).
    if engine.dialect.name == "postgresql":
        # Postgres: use EXTRACT(EPOCH ...) on the interval
        severe_minutes_expr = func.sum(
            case(
                (
                    TrafficMetric.congestion_level == "SEVERE",
                    extract("epoch", TrafficMetric.window_end - TrafficMetric.window_start)
                    / 60.0,
                ),
                else_=0.0,
            )
        )
    else:
        # SQLite (and others): use julianday() difference in minutes
        severe_minutes_expr = func.sum(
            case(
                (
                    TrafficMetric.congestion_level == "SEVERE",
                    (
                        func.julianday(TrafficMetric.window_end)
                        - func.julianday(TrafficMetric.window_start)
                    )
                    * 24
                    * 60,
                ),
                else_=0.0,
            )
        )

    top_stmt = (
        select(
            TrafficMetric.camera_id,
            func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0).label("avg_score"),
            severe_minutes_expr.label("severe_minutes"),
        )
        .where(
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= start,
            TrafficMetric.window_start <= end,
        )
        .group_by(TrafficMetric.camera_id)
        .order_by(func.avg(TrafficMetric.congestion_score).desc())
        .limit(10)
    )
    top_rows = db.execute(top_stmt).all()

    # Look up road_segment per camera (best-effort)
    from shared.models import Camera

    cam_segments: dict[str, str | None] = {}
    if top_rows:
        ids = [r[0] for r in top_rows]
        cam_stmt = select(Camera.camera_id, Camera.road_segment).where(Camera.camera_id.in_(ids))
        for cid, seg in db.execute(cam_stmt).all():
            cam_segments[cid] = seg

    top_segments = [
        TopSegment(
            camera_id=r[0],
            road_segment=cam_segments.get(r[0]),
            avg_congestion_score=float(r[1] or 0.0),
            severe_minutes=float(r[2] or 0.0),
        )
        for r in top_rows
    ]

    # Incident pie — alert count per severity in range
    pie_stmt = (
        select(Alert.severity, func.count())
        .where(Alert.triggered_at >= start, Alert.triggered_at <= end)
        .group_by(Alert.severity)
    )
    incident_pie = [
        IncidentSlice(severity=row[0], count=int(row[1])) for row in db.execute(pie_stmt).all()
    ]

    return AnalyticsMetrics(
        range_start=start,
        range_end=end,
        avg_congestion_score=avg_score,
        peak_hour_distribution=peak_hour_distribution,
        top_segments=top_segments,
        incident_pie=incident_pie,
    )


@router.get("/metrics", response_model=AnalyticsMetrics)
def analytics_metrics(
    start: datetime = Query(..., alias="from"),
    end: datetime = Query(..., alias="to"),
    db: Session = Depends(get_db),
):
    return _compute_metrics(db, start, end)


@router.get("/compare", response_model=AnalyticsCompare)
def analytics_compare(
    a_start: datetime = Query(..., alias="aFrom"),
    a_end: datetime = Query(..., alias="aTo"),
    b_start: datetime = Query(..., alias="bFrom"),
    b_end: datetime = Query(..., alias="bTo"),
    db: Session = Depends(get_db),
):
    return AnalyticsCompare(
        range_a=_compute_metrics(db, a_start, a_end),
        range_b=_compute_metrics(db, b_start, b_end),
    )


@router.get("/report/pdf")
def analytics_report_pdf(
    start: datetime = Query(..., alias="from"),
    end: datetime = Query(..., alias="to"),
    db: Session = Depends(get_db),
):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="PDF rendering requires reportlab; install via requirements/api.txt",
        )

    metrics = _compute_metrics(db, start, end)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="ITS Traffic Analytics Report")
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("ITS Traffic Analytics Report", styles["Title"]))
    story.append(
        Paragraph(
            f"Range: {metrics.range_start.isoformat()} &rarr; {metrics.range_end.isoformat()}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 12))

    story.append(
        Paragraph(
            f"<b>Average congestion score:</b> {metrics.avg_congestion_score:.3f}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Top segments</b>", styles["Heading2"]))
    top_table = [["Camera", "Road segment", "Avg score", "Severe min"]]
    for s in metrics.top_segments:
        top_table.append(
            [
                s.camera_id,
                s.road_segment or "-",
                f"{s.avg_congestion_score:.3f}",
                f"{s.severe_minutes:.1f}",
            ]
        )
    table = Table(top_table, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Peak hour distribution</b>", styles["Heading2"]))
    peak_table = [["Hour (UTC)", "Avg vehicles", "Avg score"]]
    for h in metrics.peak_hour_distribution:
        peak_table.append(
            [
                str(h.hour),
                f"{h.avg_vehicle_count:.1f}",
                f"{h.avg_congestion_score:.3f}",
            ]
        )
    p_table = Table(peak_table, hAlign="LEFT")
    p_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(p_table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Alerts by severity</b>", styles["Heading2"]))
    pie_table = [["Severity", "Count"]]
    for slice_ in metrics.incident_pie:
        pie_table.append([slice_.severity, str(slice_.count)])
    pie_t = Table(pie_table, hAlign="LEFT")
    pie_t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(pie_t)

    doc.build(story)
    buf.seek(0)

    filename = f"its_analytics_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buf, media_type="application/pdf", headers=headers)
