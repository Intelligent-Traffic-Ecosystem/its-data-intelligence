"""Single iteration of the processor loop, extracted for testability.

Tests can call ``run_iteration`` deterministically against a real Kafka
container without spinning up the whole ``main()`` loop.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from processor.aggregator import WindowAggregator
from processor.metrics_prom import EVENTS_DROPPED, EVENTS_PROCESSED, WINDOWS_FLUSHED
from processor.speed_tracker import SpeedTracker
from processor.validator import validate_event
from processor.writer import BatchedRawWriter

logger = logging.getLogger(__name__)


def run_iteration(
    consumer,
    aggregator: WindowAggregator,
    raw_writer: BatchedRawWriter,
    tracker: SpeedTracker,
    poll_timeout_ms: int = 1000,
) -> int:
    """Poll Kafka once, validate + buffer, flush due batches and expired windows.

    Returns the number of validated events processed in this iteration.
    """
    records = consumer.poll(timeout_ms=poll_timeout_ms)

    processed = 0
    for _topic_partition, messages in records.items():
        for message in messages:
            event = validate_event(message.value)
            if event is None:
                EVENTS_DROPPED.labels(reason="validation").inc()
                continue

            if event.speed_estimate is None:
                est = tracker.estimate(event)
                if est is not None:
                    event.speed_estimate = est
            else:
                tracker.estimate(event)

            aggregator.add_event(event)
            raw_writer.add(event)
            EVENTS_PROCESSED.inc()
            processed += 1

    flushed = aggregator.flush_expired()
    if flushed:
        WINDOWS_FLUSHED.inc(len(flushed))
    raw_writer.flush_due()
    tracker.evict_stale(datetime.now(timezone.utc))

    if not records:
        time.sleep(0.1)

    return processed
