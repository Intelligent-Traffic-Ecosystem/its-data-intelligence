"""Windowed aggregation of traffic events.

Collects validated events into time-based windows (default 5 seconds) per
camera. Each event is added to two windows when it carries a lane_id:
1. The camera-wide window (lane_id=None) — produces the global metric row.
2. The (camera_id, lane_id) window — produces a per-lane breakdown row.

When migrating to PyFlink, replace the in-memory dicts with Flink's
TumblingEventTimeWindows; the metric and congestion code stays the same.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone

from processor.congestion import classify_congestion
from processor.metrics import compute_metrics
from processor.writer import write_metrics
from shared.config import settings
from shared.schemas import TrafficEventInput

logger = logging.getLogger(__name__)


class WindowAggregator:
    def __init__(self, window_size: int | None = None, allowed_lateness_seconds: int | None = None):
        self.window_size = window_size or settings.window_size_seconds
        self.allowed_lateness_seconds = (
            allowed_lateness_seconds
            if allowed_lateness_seconds is not None
            else settings.window_allowed_lateness_seconds
        )
        # camera-wide: {camera_id: {window_key: [events]}}
        self._windows: dict[str, dict[int, list[TrafficEventInput]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # per-lane: {(camera_id, lane_id): {window_key: [events]}}
        self._lane_windows: dict[tuple[str, int], dict[int, list[TrafficEventInput]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # per-camera max event-time seen (epoch seconds), for watermarking.
        self._max_event_epoch_by_camera: dict[str, int] = {}

    def _window_key(self, ts: datetime) -> int:
        epoch = int(ts.timestamp())
        return epoch - (epoch % self.window_size)

    def add_event(self, event: TrafficEventInput) -> None:
        wk = self._window_key(event.timestamp)
        event_epoch = int(event.timestamp.timestamp())
        prev = self._max_event_epoch_by_camera.get(event.camera_id)
        if prev is None or event_epoch > prev:
            self._max_event_epoch_by_camera[event.camera_id] = event_epoch

        self._windows[event.camera_id][wk].append(event)
        if event.lane_id is not None:
            self._lane_windows[(event.camera_id, event.lane_id)][wk].append(event)

    def flush_expired(self) -> list[dict]:
        results: list[dict] = []

        for camera_id in list(self._windows.keys()):
            watermark = self._max_event_epoch_by_camera.get(camera_id)
            if watermark is None:
                continue
            watermark -= self.allowed_lateness_seconds
            for wk in list(self._windows[camera_id].keys()):
                if wk + self.window_size <= watermark:
                    events = self._windows[camera_id].pop(wk)
                    if events:
                        results.append(self._process_window(camera_id, wk, events, None))

        for camera_id, lane_id in list(self._lane_windows.keys()):
            watermark = self._max_event_epoch_by_camera.get(camera_id)
            if watermark is None:
                continue
            watermark -= self.allowed_lateness_seconds
            buckets = self._lane_windows[(camera_id, lane_id)]
            for wk in list(buckets.keys()):
                if wk + self.window_size <= watermark:
                    events = buckets.pop(wk)
                    if events:
                        results.append(self._process_window(camera_id, wk, events, lane_id))
            if not buckets:
                self._lane_windows.pop((camera_id, lane_id), None)

        for camera_id in list(self._windows.keys()):
            if not self._windows[camera_id]:
                self._windows.pop(camera_id, None)

        if results:
            write_metrics(results)

        return results

    def flush_all(self) -> list[dict]:
        """Flush all open windows (used for graceful shutdown/tests)."""
        results: list[dict] = []

        for camera_id in list(self._windows.keys()):
            for wk in sorted(self._windows[camera_id].keys()):
                events = self._windows[camera_id].pop(wk)
                if events:
                    results.append(self._process_window(camera_id, wk, events, None))

        for camera_id, lane_id in list(self._lane_windows.keys()):
            buckets = self._lane_windows[(camera_id, lane_id)]
            for wk in sorted(buckets.keys()):
                events = buckets.pop(wk)
                if events:
                    results.append(self._process_window(camera_id, wk, events, lane_id))
            self._lane_windows.pop((camera_id, lane_id), None)

        self._windows.clear()
        self._max_event_epoch_by_camera.clear()

        if results:
            write_metrics(results)

        return results

    def _process_window(
        self,
        camera_id: str,
        window_start_epoch: int,
        events: list[TrafficEventInput],
        lane_id: int | None,
    ) -> dict:
        window_start = datetime.fromtimestamp(window_start_epoch, tz=timezone.utc)
        window_end = datetime.fromtimestamp(window_start_epoch + self.window_size, tz=timezone.utc)

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

        logger.debug(
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
