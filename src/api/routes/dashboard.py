"""Dashboard summary + recent-events endpoints (issue #34)."""

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficEvent, TrafficMetric

router = APIRouter()


_LEVEL_RANK = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "SEVERE": 3}


def _latest_camera_metrics(db: Session) -> list[TrafficMetric]:
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
    return list(db.execute(stmt).scalars().all())


@router.get("/api/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)) -> dict:
    """KPI summary across all cameras: counts, average speed, congestion mix."""
    rows = _latest_camera_metrics(db)

    total_cameras = len(rows)
    total_vehicles = sum((r.vehicle_count or 0) for r in rows)

    speeds = [r.avg_speed_kmh for r in rows if r.avg_speed_kmh is not None]
    avg_speed = round(sum(speeds) / len(speeds), 2) if speeds else 0.0

    level_counts: dict[str, int] = {"LOW": 0, "MODERATE": 0, "HIGH": 0, "SEVERE": 0}
    for r in rows:
        level_counts[r.congestion_level or "LOW"] = (
            level_counts.get(r.congestion_level or "LOW", 0) + 1
        )

    worst_camera = None
    if rows:
        worst = max(rows, key=lambda r: _LEVEL_RANK.get(r.congestion_level or "LOW", 0))
        worst_camera = {
            "camera_id": worst.camera_id,
            "congestion_level": worst.congestion_level,
            "congestion_score": worst.congestion_score,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cameras_active": total_cameras,
        "total_vehicles_last_window": total_vehicles,
        "average_speed_kmh": avg_speed,
        "congestion_breakdown": level_counts,
        "worst_camera": worst_camera,
    }


@router.get("/api/dashboard/events")
def dashboard_events(
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Most recent N raw traffic events, newest first."""
    stmt = select(TrafficEvent).order_by(desc(TrafficEvent.ts)).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "camera_id": e.camera_id,
            "timestamp": e.ts.isoformat() if e.ts else None,
            "vehicle_id": e.vehicle_id,
            "class": e.vehicle_class,
            "lane_id": e.lane_id,
            "speed_kmh": e.speed_kmh,
            "confidence": e.confidence,
        }
        for e in rows
    ]


@router.get("/api/map/heatmap")
def map_heatmap(
    minutes: int = Query(5, ge=1, le=60),
    db: Session = Depends(get_db),
) -> dict:
    """Per-camera congestion intensity over the last N minutes for a heatmap layer."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    stmt = (
        select(
            TrafficMetric.camera_id,
            func.avg(TrafficMetric.congestion_score).label("score"),
            func.avg(TrafficMetric.vehicle_count).label("avg_count"),
            func.max(TrafficMetric.window_end).label("last_window"),
        )
        .where(
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= cutoff,
        )
        .group_by(TrafficMetric.camera_id)
    )
    rows = db.execute(stmt).all()
    points = [
        {
            "camera_id": r.camera_id,
            "intensity": round(float(r.score or 0.0), 4),
            "avg_vehicle_count": round(float(r.avg_count or 0.0), 2),
            "last_window": r.last_window.isoformat() if r.last_window else None,
        }
        for r in rows
    ]
    return {"window_minutes": minutes, "points": points}


@router.get("/api/map/incidents")
def map_incidents(
    severity: str | None = Query(None, pattern="^(LOW|MODERATE|HIGH|SEVERE)$"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Active incident markers — cameras whose latest window is HIGH or SEVERE.

    Pass ?severity=HIGH|SEVERE|MODERATE|LOW to filter further.
    """
    rows = _latest_camera_metrics(db)
    incidents: list[dict] = []
    for r in rows:
        level = r.congestion_level or "LOW"
        if severity is None and level not in ("HIGH", "SEVERE"):
            continue
        if severity is not None and level != severity:
            continue
        incidents.append(
            {
                "camera_id": r.camera_id,
                "severity": level,
                "score": r.congestion_score,
                "vehicle_count": r.vehicle_count or 0,
                "queue_length": r.queue_length or 0,
                "avg_speed_kmh": r.avg_speed_kmh or 0.0,
                "counts_by_class": (
                    json.loads(r.counts_by_class) if r.counts_by_class else {}
                ),
                "window_end": r.window_end.isoformat() if r.window_end else None,
            }
        )
    return incidents
