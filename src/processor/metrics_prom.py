"""Prometheus counters for the processor.

Exposed on a separate HTTP port (default 9100) via prometheus_client's
``start_http_server`` so the processor — which is not a FastAPI app — can be
scraped by B4's Prometheus alongside the API metrics on port 8000.
"""

from prometheus_client import Counter, Gauge, Histogram

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

# Histogram buckets tuned for local Postgres / bulk writes (sub-ms to multi-second outliers)
_WRITE_BUCKETS = (
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
)

RAW_BATCH_WRITE_SECONDS = Histogram(
    "b2_raw_batch_write_seconds",
    "Wall time for bulk insert of raw traffic_events batches",
    buckets=_WRITE_BUCKETS,
)

METRICS_BATCH_WRITE_SECONDS = Histogram(
    "b2_metrics_batch_write_seconds",
    "Wall time for batched upsert of traffic_metrics rows after window flush",
    buckets=_WRITE_BUCKETS,
)
