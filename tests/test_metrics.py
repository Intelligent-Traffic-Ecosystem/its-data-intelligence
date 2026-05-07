from datetime import datetime, timezone

from processor.metrics import compute_metrics
from shared.schemas import TrafficEventInput


def _event(**kwargs) -> TrafficEventInput:
    base = {
        "camera_id": "cam_01",
        "timestamp": datetime.now(timezone.utc),
        "frame_id": 1,
        "vehicle_id": "veh_001",
        "class": "car",
        "confidence": 0.9,
        "bbox": {"x": 0, "y": 0, "w": 50, "h": 30},
        "centroid": {"x": 25, "y": 15},
    }
    base.update(kwargs)
    return TrafficEventInput.model_validate(base)


def test_empty_events():
    result = compute_metrics([])
    assert result["vehicle_count"] == 0
    assert result["avg_speed_kmh"] == 0.0


def test_single_event():
    events = [_event(speed_estimate=30.0)]
    result = compute_metrics(events)
    assert result["vehicle_count"] == 1
    assert result["avg_speed_kmh"] == 30.0
    assert result["stopped_ratio"] == 0.0


def test_stopped_vehicles():
    events = [
        _event(vehicle_id="v1", speed_estimate=2.0),
        _event(vehicle_id="v2", speed_estimate=3.0),
        _event(vehicle_id="v3", speed_estimate=40.0),
    ]
    result = compute_metrics(events)
    assert result["vehicle_count"] == 3
    assert result["queue_length"] == 2
    assert result["stopped_ratio"] > 0.5


def test_counts_by_class():
    events = [
        _event(vehicle_id="v1", **{"class": "car"}, speed_estimate=30.0),
        _event(vehicle_id="v2", **{"class": "truck"}, speed_estimate=25.0),
        _event(vehicle_id="v3", **{"class": "car"}, speed_estimate=35.0),
    ]
    result = compute_metrics(events)
    assert result["counts_by_class"]["car"] == 2
    assert result["counts_by_class"]["truck"] == 1
