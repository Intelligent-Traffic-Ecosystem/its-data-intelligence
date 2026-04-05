import pytest
from pydantic import ValidationError

from shared.schemas import TrafficEventInput, TrafficMetricOutput


def test_event_input_valid():
    data = {
        "camera_id": "cam_01",
        "timestamp": "2026-04-16T10:15:23.456Z",
        "frame_id": 100,
        "vehicle_id": "veh_001",
        "class": "car",
        "confidence": 0.93,
        "bbox": {"x": 412, "y": 178, "w": 82, "h": 46},
        "centroid": {"x": 453, "y": 201},
    }
    event = TrafficEventInput.model_validate(data)
    assert event.camera_id == "cam_01"
    assert event.vehicle_class == "car"


def test_event_input_with_optional_fields():
    data = {
        "camera_id": "cam_01",
        "timestamp": "2026-04-16T10:15:23.456Z",
        "frame_id": 100,
        "vehicle_id": "veh_001",
        "class": "truck",
        "confidence": 0.85,
        "bbox": {"x": 0, "y": 0, "w": 100, "h": 60},
        "centroid": {"x": 50, "y": 30},
        "lane_id": 2,
        "speed_estimate": 45.5,
    }
    event = TrafficEventInput.model_validate(data)
    assert event.lane_id == 2
    assert event.speed_estimate == 45.5


def test_event_input_missing_required():
    with pytest.raises(ValidationError):
        TrafficEventInput.model_validate({"camera_id": "cam_01"})


def test_event_input_confidence_range():
    data = {
        "camera_id": "cam_01",
        "timestamp": "2026-04-16T10:15:23.456Z",
        "frame_id": 100,
        "vehicle_id": "veh_001",
        "class": "car",
        "confidence": 1.5,
        "bbox": {"x": 0, "y": 0, "w": 50, "h": 30},
        "centroid": {"x": 25, "y": 15},
    }
    with pytest.raises(ValidationError):
        TrafficEventInput.model_validate(data)


def test_metric_output():
    data = {
        "camera_id": "cam_01",
        "window_start": "2026-04-16T10:15:55Z",
        "window_end": "2026-04-16T10:16:00Z",
        "vehicle_count": 42,
        "counts_by_class": {"car": 35, "truck": 5, "bus": 2},
        "avg_speed_kmh": 11.2,
        "stopped_ratio": 0.31,
        "queue_length": 17,
        "congestion_level": "HIGH",
        "congestion_score": 0.78,
    }
    metric = TrafficMetricOutput.model_validate(data)
    assert metric.congestion_level == "HIGH"
