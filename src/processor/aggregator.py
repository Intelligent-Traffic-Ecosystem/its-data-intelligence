"""Windowed aggregation of traffic events.

This module collects validated events into time-based windows (default 5 seconds)
per camera, then triggers metric computation and congestion classification when
the window closes.

NOTE: This is a simple in-memory implementation using kafka-python. When migrating
to PyFlink, replace this with Flink's TumblingEventTimeWindows. The metric
computation and congestion logic in metrics.py / congestion.py stay the same.
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from shared.schemas import TrafficEventInput
from shared.config import settings
from processor.metrics import compute_metrics
from processor.congestion import classify_congestion
from processor.writer import write_metric

logger = logging.getLogger(__name__)


class WindowAggregator:
    def __init__(self, window_size: int | None = None):
        self.window_size = window_size or settings.window_size_seconds
        # {camera_id: {window_key: [events]}}
        self._windows: dict[str, dict[int, list[TrafficEventInput]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def _window_key(self, ts: datetime) -> int:
        """Get the window start timestamp (floored to window boundary)."""
        epoch = int(ts.timestamp())
        return epoch - (epoch % self.window_size)

    def add_event(self, event: TrafficEventInput) -> None:
        """Add an event to its window. Flushes any expired windows."""
        wk = self._window_key(event.timestamp)
        self._windows[event.camera_id][wk].append(event)

    def flush_expired(self) -> list[dict]:
        """Flush all windows whose end time has passed. Returns list of computed metrics."""
        now = int(time.time())
        current_window = now - (now % self.window_size)
        results = []

        for camera_id in list(self._windows.keys()):
            for wk in list(self._windows[camera_id].keys()):
                # Window is expired if its end (wk + window_size) is in the past
                if wk + self.window_size <= current_window:
                    events = self._windows[camera_id].pop(wk)
                    if events:
                        result = self._process_window(camera_id, wk, events)
                        results.append(result)

        return results

    def _process_window(
        self, camera_id: str, window_start_epoch: int, events: list[TrafficEventInput]
    ) -> dict:
        """Compute metrics, classify congestion, and write to database."""
        window_start = datetime.fromtimestamp(window_start_epoch, tz=timezone.utc)
        window_end = datetime.fromtimestamp(
            window_start_epoch + self.window_size, tz=timezone.utc
        )

        m = compute_metrics(events)
        level, score = classify_congestion(
            m["vehicle_count"], m["avg_speed_kmh"], m["stopped_ratio"]
        )

        result = {
            "camera_id": camera_id,
            "window_start": window_start,
            "window_end": window_end,
            **m,
            "congestion_level": level,
            "congestion_score": score,
        }

        write_metric(result)

        logger.info(
            "Window [%s] camera=%s vehicles=%d speed=%.1f congestion=%s (%.2f)",
            window_start.isoformat(),
            camera_id,
            m["vehicle_count"],
            m["avg_speed_kmh"],
            level,
            score,
        )

        return result
