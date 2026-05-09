"""Congestion forecast endpoints (issue #30).

Serves short-horizon predictions via a lightweight EWMA + linear-trend
baseline so the dashboard has a working forecast surface immediately. The
heavier ST-GCN model in ``traffic-predictor/`` can later replace
``processor.forecaster.forecast`` without changing this route.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from processor.forecaster import forecast
from shared.config import settings
from shared.db import get_db
from shared.models import TrafficMetric

router = APIRouter(prefix="/api/predict", tags=["predict"])


def _recent_scores(db: Session, camera_id: str, lookback_minutes: int) -> tuple[
    list[float], datetime | None
]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    stmt = (
        select(TrafficMetric.window_start, TrafficMetric.congestion_score)
        .where(
            TrafficMetric.camera_id == camera_id,
            TrafficMetric.lane_id.is_(None),
            TrafficMetric.window_start >= cutoff,
        )
        .order_by(TrafficMetric.window_start.asc())
    )
    rows = db.execute(stmt).all()
    if not rows:
        return [], None
    return [float(r.congestion_score or 0.0) for r in rows], rows[-1].window_start


@router.get("/congestion")
def predict_congestion(
    camera_id: str = Query(..., description="Camera ID to forecast"),
    horizon_minutes: int = Query(5, ge=1, le=30, description="Forecast horizon in minutes"),
    lookback_minutes: int = Query(15, ge=1, le=120, description="History window in minutes"),
    db: Session = Depends(get_db),
) -> dict:
    """Return an N-step-ahead congestion forecast for one camera.

    The horizon is internally divided into ``window_size_seconds`` steps to
    match the live aggregator (default 5s windows → 12 steps for 1 minute).
    """
    scores, last_window = _recent_scores(db, camera_id, lookback_minutes)
    if not scores:
        raise HTTPException(
            status_code=404,
            detail=f"No recent metrics for camera {camera_id}",
        )

    step_seconds = max(1, settings.window_size_seconds)
    horizon_steps = max(1, (horizon_minutes * 60) // step_seconds)

    points = forecast(scores, horizon=horizon_steps)

    base_ts = last_window or datetime.now(timezone.utc)
    forecast_payload = [
        {
            "step": p.step,
            "predicted_at": (base_ts + timedelta(seconds=step_seconds * p.step)).isoformat(),
            "congestion_score": p.score,
            "congestion_level": p.level,
        }
        for p in points
    ]

    return {
        "camera_id": camera_id,
        "model": "ewma+trend-baseline",
        "horizon_minutes": horizon_minutes,
        "step_seconds": step_seconds,
        "history_samples": len(scores),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast": forecast_payload,
    }
