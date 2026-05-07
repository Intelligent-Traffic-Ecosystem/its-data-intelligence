from shared.config import settings


def _load_thresholds() -> tuple[float, float, float]:
    """Load current congestion thresholds.

    This indirection exists so tests (and future dynamic reloading) can swap the
    source of thresholds (e.g., database-backed values) without changing the
    classification logic.
    """
    return (
        settings.congestion_threshold_low,
        settings.congestion_threshold_moderate,
        settings.congestion_threshold_high,
    )


def normalize(value: float, max_value: float) -> float:
    """Clamp value to [0, 1] based on max_value."""
    if max_value <= 0:
        return 0.0
    return min(max(value / max_value, 0.0), 1.0)


def classify_congestion(
    vehicle_count: int, avg_speed_kmh: float, stopped_ratio: float
) -> tuple[str, float]:
    """Classify congestion level from aggregated metrics.

    Returns (level, score) where level is one of LOW/MODERATE/HIGH/SEVERE
    and score is a float in [0, 1].
    """
    score = (
        settings.congestion_weight_count * normalize(vehicle_count, settings.max_vehicle_count)
        + settings.congestion_weight_speed * (1 - normalize(avg_speed_kmh, settings.max_speed_kmh))
        + settings.congestion_weight_stopped * stopped_ratio
    )

    score = min(max(score, 0.0), 1.0)

    low, moderate, high = _load_thresholds()

    if score < low:
        level = "LOW"
    elif score < moderate:
        level = "MODERATE"
    elif score < high:
        level = "HIGH"
    else:
        level = "SEVERE"

    return level, round(score, 4)
