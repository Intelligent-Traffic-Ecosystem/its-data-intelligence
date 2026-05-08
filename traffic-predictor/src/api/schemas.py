"""
Pydantic response schemas for the prediction REST API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CongestionLabel = Literal["LOW", "MEDIUM", "HIGH"]


class LaneForecastSummary(BaseModel):
    """Summarised forecast for a single lane (for list endpoints)."""

    camera_id: str
    lane_id: int
    congestion_start: datetime | None = Field(
        None, description="Estimated UTC datetime when congestion begins"
    )
    congestion_end: datetime | None = Field(
        None, description="Estimated UTC datetime when congestion ends"
    )
    peak_congestion_probability: float = Field(
        ..., ge=0.0, le=1.0, description="Highest predicted congestion probability"
    )
    current_congestion_level: CongestionLabel = Field(
        ..., description="Predicted congestion level at the next step"
    )
    next_5min_avg_count: float = Field(
        ..., ge=0.0, description="Average predicted vehicle count over next 5 minutes"
    )
    generated_at: datetime


class LaneForecastDetail(LaneForecastSummary):
    """Full per-step forecast data for a single lane."""

    predicted_vehicle_counts: list[float] = Field(
        ..., description="Predicted vehicle count at each future 5-s window"
    )
    congestion_probabilities: list[float] = Field(
        ..., description="Congestion probability at each future 5-s window"
    )
    congestion_levels: list[CongestionLabel] = Field(
        ..., description="Predicted congestion level at each future 5-s window"
    )
    forecast_timestamps: list[datetime] = Field(
        ..., description="UTC timestamp for each future 5-s window"
    )


class ForecastResponse(BaseModel):
    """Top-level response wrapping all lane forecasts."""

    generated_at: datetime
    num_lanes: int
    forecasts: list[LaneForecastSummary]


class CongestionSummary(BaseModel):
    """Current + predicted congestion state per lane."""

    camera_id: str
    lane_id: int
    congestion_start: datetime | None
    congestion_end: datetime | None
    peak_probability: float = Field(..., ge=0.0, le=1.0)
    predicted_levels: list[CongestionLabel]


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    num_nodes: int
