from collections import Counter

from shared.schemas import TrafficEventInput

# Speed below this threshold (km/h) counts as "stopped"
STOPPED_SPEED_THRESHOLD = 5.0


def compute_metrics(events: list[TrafficEventInput]) -> dict:
    """Compute aggregated metrics from a window of events.

    Returns a dict with: vehicle_count, counts_by_class, avg_speed_kmh,
    stopped_ratio, queue_length.
    """
    if not events:
        return {
            "vehicle_count": 0,
            "counts_by_class": {},
            "avg_speed_kmh": 0.0,
            "stopped_ratio": 0.0,
            "queue_length": 0,
        }

    # Count unique vehicles in this window
    unique_vehicles = {e.vehicle_id for e in events}
    vehicle_count = len(unique_vehicles)

    # Counts by class
    counts_by_class = dict(Counter(e.vehicle_class for e in events))

    # Average speed — use speed_estimate if available, otherwise 0
    speeds = [e.speed_estimate for e in events if e.speed_estimate is not None]
    avg_speed_kmh = sum(speeds) / len(speeds) if speeds else 0.0

    # Stopped ratio — fraction of events with speed below threshold
    if speeds:
        stopped_count = sum(1 for s in speeds if s < STOPPED_SPEED_THRESHOLD)
        stopped_ratio = stopped_count / len(speeds)
    else:
        stopped_ratio = 0.0

    # Queue length — number of unique stopped vehicles
    stopped_vehicles = set()
    for e in events:
        if e.speed_estimate is not None and e.speed_estimate < STOPPED_SPEED_THRESHOLD:
            stopped_vehicles.add(e.vehicle_id)
    queue_length = len(stopped_vehicles)

    return {
        "vehicle_count": vehicle_count,
        "counts_by_class": counts_by_class,
        "avg_speed_kmh": round(avg_speed_kmh, 2),
        "stopped_ratio": round(stopped_ratio, 4),
        "queue_length": queue_length,
    }
