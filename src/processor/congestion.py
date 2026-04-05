from shared.config import settings


def normalize(value: float, max_value: float) -> float:
    """Clamp value to [0, 1] based on max_value."""
    if max_value <= 0:
        return 0.0
    return min(max(value / max_value, 0.0), 1.0)


def classify_congestion(vehicle_count: int, avg_speed_kmh: float, stopped_ratio: float) -> tuple[str, float]:
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

    if score < settings.congestion_threshold_low:
        level = "LOW"
    elif score < settings.congestion_threshold_moderate:
        level = "MODERATE"
    elif score < settings.congestion_threshold_high:
        level = "HIGH"
    else:
        level = "SEVERE"

    return level, round(score, 4)
