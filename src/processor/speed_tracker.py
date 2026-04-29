"""Centroid-based speed fallback.

When B1 omits ``speed_estimate``, B2 estimates it from inter-frame centroid
displacement of the same ``vehicle_id``. This is intentionally simple:
distance = sqrt(dx^2 + dy^2) * pixel_to_meter; speed = distance / dt * 3.6.

The pixel-to-meter calibration is global today (per-camera calibration is
flagged as open question #1 in the plan and can be layered in later via a
``CAMERA_CALIBRATIONS`` env var).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from shared.config import settings
from shared.schemas import TrafficEventInput


class SpeedTracker:
    def __init__(
        self,
        ttl_seconds: int | None = None,
        pixel_to_meter: float | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds or settings.speed_tracker_ttl_seconds
        self.pixel_to_meter = (
            pixel_to_meter
            if pixel_to_meter is not None
            else settings.speed_tracker_pixel_to_meter
        )
        # vehicle_id -> (centroid_x, centroid_y, last_ts)
        self._last: dict[str, tuple[int, int, datetime]] = {}

    def estimate(self, event: TrafficEventInput) -> float | None:
        """Return km/h estimate or None on first sighting / zero dt.

        Always updates the last-seen centroid so subsequent calls have a base.
        """
        prev = self._last.get(event.vehicle_id)
        cx, cy = event.centroid.x, event.centroid.y
        ts = event.timestamp
        self._last[event.vehicle_id] = (cx, cy, ts)

        if prev is None:
            return None

        px, py, prev_ts = prev
        dt = (ts - prev_ts).total_seconds()
        if dt <= 0:
            return None

        distance_pixels = math.hypot(cx - px, cy - py)
        distance_m = distance_pixels * self.pixel_to_meter
        return (distance_m / dt) * 3.6

    def evict_stale(self, now: datetime | None = None) -> int:
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - self.ttl_seconds
        stale = [vid for vid, (_, _, ts) in self._last.items() if ts.timestamp() < cutoff]
        for vid in stale:
            self._last.pop(vid, None)
        return len(stale)
