"""Prometheus counters for the processor.

Exposed on a separate HTTP port (default 9100) via prometheus_client's
``start_http_server`` so the processor — which is not a FastAPI app — can be
scraped by B4's Prometheus alongside the API metrics on port 8000.
"""

from prometheus_client import Counter, Gauge

EVENTS_PROCESSED = Counter(
    "b2_events_processed_total",
    "Validated B1 events processed by the stream processor",
)

EVENTS_DROPPED = Counter(
    "b2_events_dropped_total",
    "Events dropped by the stream processor",
    ["reason"],
)

WINDOWS_FLUSHED = Counter(
    "b2_window_flushes_total",
    "Tumbling windows flushed and written to the metrics table",
)

KAFKA_CONSUMER_LAG = Gauge(
    "b2_kafka_consumer_lag",
    "Kafka consumer lag in messages, summed across partitions",
)
