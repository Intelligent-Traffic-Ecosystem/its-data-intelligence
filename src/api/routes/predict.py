"""Congestion forecast endpoint backed by ST-GCN (``traffic-predictor``)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from shared.config import settings
from shared.db import get_db
from shared.stgcn_predictor import StgcnInferenceError, forecast_camera_stgcn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/predict", tags=["predict"])


@router.get("/congestion")
def predict_congestion(
    camera_id: str = Query(..., description="Camera ID to forecast"),
    horizon_minutes: int = Query(10, ge=1, le=30, description="Forecast horizon in minutes"),
    lookback_minutes: int = Query(15, ge=1, le=120, description="History window in minutes"),
    db: Session = Depends(get_db),
) -> dict:
    """Return an N-step-ahead congestion forecast for one camera.

    Uses the ST-GCN from ``traffic-predictor/``, reading per-lane
    ``traffic_metrics`` rows over ``lookback_minutes`` and aggregating lane
    forecasts into a single camera-level series. Step size follows the ST-GCN
    config (``features.window_size_seconds``), which must match the trained
    checkpoint.
    """
    try:
        payload = forecast_camera_stgcn(
            db,
            settings,
            camera_id=camera_id,
            lookback_minutes=lookback_minutes,
            horizon_minutes=horizon_minutes,
        )
    except StgcnInferenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception:
        logger.exception("stgcn_predict_failed camera_id=%s", camera_id)
        raise HTTPException(
            status_code=503,
            detail="ST-GCN inference failed; check logs, checkpoint, and dependencies.",
        ) from None

    if payload.get("history_samples", 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No recent per-lane metrics for camera {camera_id}",
        )

    return payload
