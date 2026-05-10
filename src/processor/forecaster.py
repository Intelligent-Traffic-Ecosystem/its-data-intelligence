"""Lightweight short-term congestion forecaster (issue #30).

This is a simple, dependency-free baseline that the API can serve while the
heavier ST-GCN trainer in ``traffic-predictor/`` is being trained. Given a
recent series of congestion scores (one per 5-second window) it produces an
N-step-ahead forecast using:

  - exponentially-weighted moving average (level), and
  - linear trend over the last ``trend_lookback`` samples.

The forecast is bounded to [0, 1] and labelled by the same thresholds as the
real-time classifier so the dashboard can render it identically.

Replaceable: the public surface is just ``forecast(scores, ...)`` so an
ST-GCN-based predictor can drop in later without touching the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.config import settings


@dataclass
class ForecastPoint:
    step: int  # 1 = next window
    score: float
    level: str


def _label(score: float) -> str:
    if score < settings.congestion_threshold_low:
        return "LOW"
    if score < settings.congestion_threshold_moderate:
        return "MODERATE"
    if score < settings.congestion_threshold_high:
        return "HIGH"
    return "SEVERE"


def _ewma(values: list[float], alpha: float = 0.4) -> float:
    if not values:
        return 0.0
    level = values[0]
    for v in values[1:]:
        level = alpha * v + (1 - alpha) * level
    return level


def _linear_slope(values: list[float]) -> float:
    """OLS slope of values vs index. Returns 0.0 if fewer than 2 samples."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def forecast(
    scores: list[float],
    horizon: int = 6,
    trend_lookback: int = 6,
    alpha: float = 0.4,
) -> list[ForecastPoint]:
    """Predict the next ``horizon`` congestion scores.

    Args:
        scores: chronological list of recent congestion scores (oldest first).
        horizon: number of future windows to project.
        trend_lookback: how many recent samples feed the linear-trend term.
        alpha: EWMA smoothing factor for the level estimate.
    """
    if horizon <= 0:
        return []
    if not scores:
        return [ForecastPoint(step=i + 1, score=0.0, level="LOW") for i in range(horizon)]

    level = _ewma(scores, alpha=alpha)
    slope = _linear_slope(scores[-trend_lookback:])

    out: list[ForecastPoint] = []
    for step in range(1, horizon + 1):
        projected = level + slope * step
        projected = max(0.0, min(1.0, projected))
        out.append(
            ForecastPoint(step=step, score=round(projected, 4), level=_label(projected))
        )
    return out
