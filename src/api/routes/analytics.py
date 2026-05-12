from datetime import UTC, datetime, timedelta
from io import BytesIO

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import Integer, case, cast, extract, func, select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import CameraRegistry, TrafficMetric
from shared.schemas import (
    AnalyticsCompareResponse,
    AnalyticsMetricsResponse,
    AnalyticsRangeSummary,
    IncidentSummary,
    PeakHourTrend,
    TopSegment,
)

router = APIRouter(prefix="/api/analytics")


def _default_window() -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - timedelta(hours=24), end


def _window_filters(start: datetime, end: datetime):
    return (
        TrafficMetric.lane_id.is_(None),
        TrafficMetric.window_start >= start,
        TrafficMetric.window_start <= end,
    )


def _incident_case():
    return case(
        (
            TrafficMetric.congestion_level.in_(["HIGH", "SEVERE"]),
            1,
        ),
        else_=0,
    )


def _range_summary(db: Session, start: datetime, end: datetime) -> AnalyticsRangeSummary:
    incident_case = _incident_case()
    summary_stmt = select(
        func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0).label("avg_congestion"),
        func.coalesce(func.sum(TrafficMetric.vehicle_count), 0).label("vehicle_count"),
        func.coalesce(func.avg(TrafficMetric.avg_speed_kmh), 0.0).label("avg_speed"),
        func.coalesce(func.sum(incident_case), 0).label("incident_count"),
    ).where(*_window_filters(start, end))
    summary = db.execute(summary_stmt).one()

    peak_hour_stmt = (
        select(
            cast(extract("hour", TrafficMetric.window_start), Integer).label("hour"),
            func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0).label("avg_congestion"),
        )
        .where(*_window_filters(start, end))
        .group_by("hour")
        .order_by(func.avg(TrafficMetric.congestion_score).desc())
        .limit(1)
    )
    peak_hour_row = db.execute(peak_hour_stmt).one_or_none()

    return AnalyticsRangeSummary(
        start=start,
        end=end,
        average_congestion=float(summary.avg_congestion or 0.0),
        vehicle_count=int(summary.vehicle_count or 0),
        average_speed_kmh=float(summary.avg_speed or 0.0),
        incident_count=int(summary.incident_count or 0),
        peak_hour=int(peak_hour_row.hour) if peak_hour_row else None,
    )


def _build_reportlab_pdf(title: str, lines: list[str]) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, title)
    y -= 30
    pdf.setFont("Helvetica", 11)

    for line in lines:
        if y < 60:
            pdf.showPage()
            pdf.setFont("Helvetica", 11)
            y = height - 50
        pdf.drawString(50, y, line)
        y -= 18

    pdf.save()
    return buffer.getvalue()


@router.get("/metrics", response_model=AnalyticsMetricsResponse)
def get_analytics_metrics(
    start: datetime | None = Query(None, description="Start datetime (ISO 8601)"),
    end: datetime | None = Query(None, description="End datetime (ISO 8601)"),
    db: Session = Depends(get_db),
):
    if start is None or end is None:
        start, end = _default_window()

    incident_case = _incident_case()
    summary_stmt = select(
        func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0).label("avg_congestion"),
        func.coalesce(
            func.sum(
                case((TrafficMetric.congestion_level == "HIGH", 1), else_=0)
            ),
            0,
        ).label("high_count"),
        func.coalesce(
            func.sum(
                case((TrafficMetric.congestion_level == "SEVERE", 1), else_=0)
            ),
            0,
        ).label("severe_count"),
        func.coalesce(func.sum(incident_case), 0).label("incident_count"),
    ).where(*_window_filters(start, end))
    summary = db.execute(summary_stmt).one()

    trends_stmt = (
        select(
            cast(extract("hour", TrafficMetric.window_start), Integer).label("hour"),
            func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0).label(
                "average_congestion"
            ),
            func.coalesce(func.avg(TrafficMetric.avg_speed_kmh), 0.0).label(
                "average_speed_kmh"
            ),
            func.coalesce(func.sum(TrafficMetric.vehicle_count), 0).label("vehicle_count"),
        )
        .where(*_window_filters(start, end))
        .group_by("hour")
        .order_by("hour")
    )
    trends_rows = db.execute(trends_stmt).all()

    top_segments_stmt = (
        select(
            func.coalesce(CameraRegistry.road_segment, TrafficMetric.camera_id).label(
                "segment_key"
            ),
            func.coalesce(func.avg(TrafficMetric.congestion_score), 0.0).label(
                "average_congestion"
            ),
            func.coalesce(
                func.sum(
                    case((TrafficMetric.congestion_level == "SEVERE", 5), else_=0)
                ),
                0,
            ).label("severe_minutes"),
            func.coalesce(func.sum(incident_case), 0).label("incident_count"),
            func.coalesce(func.avg(TrafficMetric.queue_length), 0.0).label(
                "average_queue_length"
            ),
        )
        .join(CameraRegistry, CameraRegistry.camera_id == TrafficMetric.camera_id, isouter=True)
        .where(*_window_filters(start, end))
        .group_by("segment_key")
        .order_by(
            func.avg(TrafficMetric.congestion_score).desc(),
            func.sum(case((TrafficMetric.congestion_level == "SEVERE", 5), else_=0)).desc(),
        )
        .limit(10)
    )
    top_segments_rows = db.execute(top_segments_stmt).all()

    return AnalyticsMetricsResponse(
        start=start,
        end=end,
        average_congestion=float(summary.avg_congestion or 0.0),
        peak_hour_trends=[
            PeakHourTrend(
                hour=int(row.hour),
                average_congestion=float(row.average_congestion or 0.0),
                average_speed_kmh=float(row.average_speed_kmh or 0.0),
                vehicle_count=int(row.vehicle_count or 0),
            )
            for row in trends_rows
        ],
        top_segments=[
            TopSegment(
                camera_id=row.segment_key,
                lane_id=None,
                average_congestion=float(row.average_congestion or 0.0),
                average_queue_length=float(row.average_queue_length or 0.0),
                incident_count=int(row.incident_count or 0),
            )
            for row in top_segments_rows
        ],
        incidents=IncidentSummary(
            total_incidents=int(summary.incident_count or 0),
            high=int(summary.high_count or 0),
            critical=int(summary.severe_count or 0),
        ),
    )


@router.get("/compare", response_model=AnalyticsCompareResponse)
def compare_analytics_ranges(
    start_a: datetime = Query(..., description="Range A start datetime (ISO 8601)"),
    end_a: datetime = Query(..., description="Range A end datetime (ISO 8601)"),
    start_b: datetime = Query(..., description="Range B start datetime (ISO 8601)"),
    end_b: datetime = Query(..., description="Range B end datetime (ISO 8601)"),
    db: Session = Depends(get_db),
):
    summary_a = _range_summary(db, start_a, end_a)
    summary_b = _range_summary(db, start_b, end_b)
    return AnalyticsCompareResponse(
        range_a=summary_a,
        range_b=summary_b,
        congestion_delta=summary_b.average_congestion - summary_a.average_congestion,
        vehicle_count_delta=summary_b.vehicle_count - summary_a.vehicle_count,
        speed_delta=summary_b.average_speed_kmh - summary_a.average_speed_kmh,
        incident_delta=summary_b.incident_count - summary_a.incident_count,
    )


@router.get("/report/pdf")
def download_analytics_report_pdf(
    start: datetime | None = Query(None, description="Start datetime (ISO 8601)"),
    end: datetime | None = Query(None, description="End datetime (ISO 8601)"),
    db: Session = Depends(get_db),
):
    if start is None or end is None:
        start, end = _default_window()

    summary = _range_summary(db, start, end)
    lines = [
        f"Period: {summary.start.isoformat()} to {summary.end.isoformat()}",
        f"Average congestion: {summary.average_congestion:.2f}",
        f"Average speed (km/h): {summary.average_speed_kmh:.2f}",
        f"Vehicle count: {summary.vehicle_count}",
        f"Incident count: {summary.incident_count}",
        f"Peak hour: {summary.peak_hour if summary.peak_hour is not None else 'N/A'}",
    ]
    pdf_bytes = _build_reportlab_pdf("Traffic Analytics Report", lines)

    filename = f"analytics-report-{start.date().isoformat()}-{end.date().isoformat()}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers=headers,
    )
