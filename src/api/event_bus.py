"""In-process pub/sub bus for real-time WS event fan-out (issue #35).

Other modules call ``bus.publish(event_type, payload)`` to push events to all
subscribed WebSocket clients on ``/ws/events``. The bus is async-safe and
non-blocking — slow clients drop messages instead of stalling producers.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


_VALID_EVENT_TYPES = {
    "traffic_metrics_update",
    "heatmap_update",
    "new_alert",
    "admin_broadcast",
}


class EventBus:
    """Fan-out bus backed by per-subscriber asyncio queues."""

    def __init__(self, queue_size: int = 100) -> None:
        self._subs: list[asyncio.Queue] = []
        self._queue_size = queue_size

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.append(q)
        logger.info("event_bus_subscribe total=%d", len(self._subs))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)
        logger.info("event_bus_unsubscribe remaining=%d", len(self._subs))

    def publish(self, event_type: str, data: Any) -> int:
        """Drop a typed envelope onto every subscriber queue.

        Returns the number of subscribers that received the event. Subscribers
        whose queue is full are skipped (slow consumer protection).
        """
        if event_type not in _VALID_EVENT_TYPES:
            raise ValueError(f"unknown event_type: {event_type}")

        envelope = {
            "event": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        delivered = 0
        for q in list(self._subs):
            try:
                q.put_nowait(envelope)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning("event_bus_drop slow_subscriber event=%s", event_type)
        return delivered

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


bus = EventBus()
