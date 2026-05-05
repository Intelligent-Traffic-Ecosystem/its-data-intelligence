"""Windowed aggregation of traffic events.

Collects validated events into time-based windows (default 5 seconds) per
camera. Each event is added to two windows when it carries a lane_id:
1. The camera-wide window (lane_id=None) — produces the global metric row.
2. The (camera_id, lane_id) window — produces a per-lane breakdown row.

When migrating to PyFlink, replace the in-memory dicts with Flink's
TumblingEventTimeWindows; the metric and congestion code stays the same.
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
        # camera-wide: {camera_id: {window_key: [events]}}
        self._windows: dict[str, dict[int, list[TrafficEventInput]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # per-lane: {(camera_id, lane_id): {window_key: [events]}}
        self._lane_windows: dict[
                   tuple[str, int], dict[int, list[TrafficEventInput]]
        ] = defaultdict(lambda: defaultdict(list))

    def _window_key(self, ts: datetime) -> int:
        epoch = int(ts.timestamp())
        return epoch - (epoch % self.window_size)

    def add_event(self, event: TrafficEventInput) -> None:
        wk = self._window_key(event.timestamp)
        self._windows[event.camera_id][wk].append(event)
        if event.lane_id is not None:
            self._lane_windows[(event.camera_id, event.lane_id)][wk].append(event)

    def flush_expired(self) -> list[dict]:
        now = int(time.time())
        current_window = now - (now % self.window_size)
        results: list[dict] = []

        for camera_id in list(self._windows.keys()):
            for wk in list(self._windows[camera_id].keys()):
                if wk + self.window_size <= current_window:
                    events = self._windows[camera_id].pop(wk)
                    if events:
                        results.append(self._process_window(camera_id, wk, events, None))

        for (camera_id, lane_id) in list(self._lane_windows.keys()):
            buckets = self._lane_windows[(camera_id, lane_id)]
            for wk in list(buckets.keys()):
                if wk + self.window_size <= current_window:
                    events = buckets.pop(wk)
                    if events:
                        results.append(
                            self._process_window(camera_id, wk, events, lane_id)
                        )
            if not buckets:
                self._lane_windows.pop((camera_id, lane_id), None)

        return results

    def _process_window(
        self,
        camera_id: str,
        window_start_epoch: int,
        events: list[TrafficEventInput],
        lane_id: int | None,
    ) -> dict:
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
            "lane_id": lane_id,
            "window_start": window_start,
            "window_end": window_end,
            **m,
            "congestion_level": level,
            "congestion_score": score,
        }

        write_metric(result)

        logger.info(
            "window_flushed camera=%s lane=%s start=%s vehicles=%d speed=%.1f congestion=%s score=%.2f",
            camera_id,
            lane_id,
            window_start.isoformat(),
            m["vehicle_count"],
            m["avg_speed_kmh"],
            level,
            score,
        )

        return result
