from datetime import UTC, datetime, timedelta

import pytest

from processor.speed_tracker import SpeedTracker
from shared.schemas import BBox, Centroid, TrafficEventInput


def _event(camera_id: str, vehicle_id: str, ts: datetime, x: float, y: float):
    return TrafficEventInput(
        camera_id=camera_id,
        timestamp=ts,
        frame_id=1,
        vehicle_id=vehicle_id,
        **{"class": "car"},
        confidence=0.9,
        bbox=BBox(x=0, y=0, w=10, h=10),
        centroid=Centroid(x=x, y=y),
    )


def test_speed_tracker_uses_camera_specific_calibration():
    tracker = SpeedTracker(
        pixel_to_meter=0.05,
        camera_calibrations={"cam_01": 0.10},
    )

    start = datetime(2026, 5, 7, tzinfo=UTC)
    assert tracker.estimate(_event("cam_01", "veh_1", start, 0, 0)) is None

    speed = tracker.estimate(_event("cam_01", "veh_1", start + timedelta(seconds=1), 10, 0))

    assert speed == pytest.approx(3.6)


def test_speed_tracker_falls_back_to_default_calibration():
    tracker = SpeedTracker(
        pixel_to_meter=0.05,
        camera_calibrations={"cam_01": 0.10},
    )

    start = datetime(2026, 5, 7, tzinfo=UTC)
    assert tracker.estimate(_event("cam_02", "veh_1", start, 0, 0)) is None

    speed = tracker.estimate(_event("cam_02", "veh_1", start + timedelta(seconds=1), 10, 0))

    assert speed == pytest.approx(1.8)
