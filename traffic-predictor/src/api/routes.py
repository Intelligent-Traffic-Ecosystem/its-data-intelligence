"""
FastAPI route handlers for the prediction API.

Endpoints
---------
GET /health
    Liveness / readiness check.

GET /predict
    Run a fresh prediction cycle and return summaries for all lanes.

GET /predict/{camera_id}/{lane_id}
    Run prediction and return the full per-step detail for one lane.

GET /congestion
    Return estimated congestion start/end times for all lanes.

GET /congestion/{camera_id}/{lane_id}
    Return congestion details for a specific lane.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from src.api.schemas import (
    CongestionSummary,
    ForecastResponse,
    HealthResponse,
    LaneForecastDetail,
    LaneForecastSummary,
)
from src.inference.predictor import LaneForecast

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_summary(fc: LaneForecast) -> LaneForecastSummary:
    next_5min_steps = min(60, len(fc.predicted_vehicle_counts))  # 60 * 5s = 5 min
    avg_count = (
        sum(fc.predicted_vehicle_counts[:next_5min_steps]) / next_5min_steps
        if next_5min_steps > 0 else 0.0
    )
    current_level = fc.congestion_levels[0] if fc.congestion_levels else "LOW"
    return LaneForecastSummary(
        camera_id=fc.camera_id,
        lane_id=fc.lane_id,
        congestion_start=fc.congestion_start,
        congestion_end=fc.congestion_end,
        peak_congestion_probability=fc.peak_congestion_probability,
        current_congestion_level=current_level,
        next_5min_avg_count=round(avg_count, 2),
        generated_at=fc.generated_at,
    )


def _to_detail(fc: LaneForecast) -> LaneForecastDetail:
    summary = _to_summary(fc)
    return LaneForecastDetail(
        **summary.model_dump(),
        predicted_vehicle_counts=fc.predicted_vehicle_counts,
        congestion_probabilities=fc.congestion_probabilities,
        congestion_levels=fc.congestion_levels,
        forecast_timestamps=fc.forecast_timestamps,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health(request: Request) -> HealthResponse:
    predictor = request.app.state.predictor
    return HealthResponse(
        status="ok",
        model_loaded=predictor is not None,
        num_nodes=predictor._graph.num_nodes if predictor else 0,
    )


# ---------------------------------------------------------------------------
# Prediction endpoints
# ---------------------------------------------------------------------------


@router.get("/predict", response_model=ForecastResponse, tags=["forecast"])
async def predict_all(request: Request) -> ForecastResponse:
    """
    Run a fresh inference cycle and return a forecast summary for every lane.
    """
    predictor = request.app.state.predictor
    forecasts: list[LaneForecast] = predictor.predict()
    return ForecastResponse(
        generated_at=datetime.now(tz=timezone.utc),
        num_lanes=len(forecasts),
        forecasts=[_to_summary(fc) for fc in forecasts],
    )


@router.get(
    "/predict/{camera_id}/{lane_id}",
    response_model=LaneForecastDetail,
    tags=["forecast"],
)
async def predict_lane(
    camera_id: str, lane_id: int, request: Request
) -> LaneForecastDetail:
    """
    Return the full per-step forecast for a specific (camera, lane).
    """
    predictor = request.app.state.predictor
    forecasts: list[LaneForecast] = predictor.predict()
    for fc in forecasts:
        if fc.camera_id == camera_id and fc.lane_id == lane_id:
            return _to_detail(fc)
    raise HTTPException(
        status_code=404,
        detail=f"Lane (camera={camera_id}, lane_id={lane_id}) not found in graph.",
    )


# ---------------------------------------------------------------------------
# Congestion endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/congestion",
    response_model=list[CongestionSummary],
    tags=["congestion"],
)
async def congestion_all(request: Request) -> list[CongestionSummary]:
    """
    Return estimated congestion start/end windows for all lanes.
    """
    predictor = request.app.state.predictor
    forecasts: list[LaneForecast] = predictor.predict()
    return [
        CongestionSummary(
            camera_id=fc.camera_id,
            lane_id=fc.lane_id,
            congestion_start=fc.congestion_start,
            congestion_end=fc.congestion_end,
            peak_probability=fc.peak_congestion_probability,
            predicted_levels=fc.congestion_levels[::12],  # 1-min resolution
        )
        for fc in forecasts
    ]


@router.get(
    "/congestion/{camera_id}/{lane_id}",
    response_model=CongestionSummary,
    tags=["congestion"],
)
async def congestion_lane(
    camera_id: str, lane_id: int, request: Request
) -> CongestionSummary:
    """
    Return congestion start/end for a specific (camera, lane).
    """
    predictor = request.app.state.predictor
    forecasts: list[LaneForecast] = predictor.predict()
    for fc in forecasts:
        if fc.camera_id == camera_id and fc.lane_id == lane_id:
            return CongestionSummary(
                camera_id=fc.camera_id,
                lane_id=fc.lane_id,
                congestion_start=fc.congestion_start,
                congestion_end=fc.congestion_end,
                peak_probability=fc.peak_congestion_probability,
                predicted_levels=fc.congestion_levels[::12],
            )
    raise HTTPException(
        status_code=404,
        detail=f"Lane (camera={camera_id}, lane_id={lane_id}) not found in graph.",
    )
