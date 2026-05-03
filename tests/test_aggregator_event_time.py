from datetime import datetime, timedelta, timezone

from processor.aggregator import WindowAggregator
from shared.schemas import TrafficEventInput


def _event(ts: datetime, vehicle_id: str) -> TrafficEventInput:
    return TrafficEventInput.model_validate(
        {
            "camera_id": "cam_01",
            "timestamp": ts,
            "frame_id": 1,
            "vehicle_id": vehicle_id,
            "class": "car",
            "confidence": 0.95,
            "bbox": {"x": 0, "y": 0, "w": 20, "h": 10},
            "centroid": {"x": 10, "y": 5},
        }
    )


def test_flush_uses_event_time_watermark():
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    agg = WindowAggregator(window_size=5, allowed_lateness_seconds=1)

    # Window [00:00:00, 00:00:05)
    agg.add_event(_event(base + timedelta(seconds=1), "v1"))
    # Window [00:00:05, 00:00:10) advances watermark enough to close first window.
    agg.add_event(_event(base + timedelta(seconds=8), "v2"))

    flushed = agg.flush_expired()
    camera_rows = [r for r in flushed if r["lane_id"] is None]
    assert len(camera_rows) == 1
    assert camera_rows[0]["window_start"] == base


def test_allowed_lateness_keeps_previous_window_open():
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    agg = WindowAggregator(window_size=5, allowed_lateness_seconds=3)

    # Event in first window
    agg.add_event(_event(base + timedelta(seconds=1), "v1"))
    # Event in next window, but not far enough to surpass lateness threshold.
    agg.add_event(_event(base + timedelta(seconds=6), "v2"))

    flushed = agg.flush_expired()
    assert flushed == []
