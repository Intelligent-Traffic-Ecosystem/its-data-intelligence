"""B2 Stream Processor — entry point.

Consumes traffic events from Kafka, aggregates them in time windows,
computes metrics, classifies congestion, persists raw events + metrics to
PostgreSQL, and periodically prunes data per the SRS retention policy.

Run: python -m processor.main
"""

import logging
import time

from prometheus_client import start_http_server

from processor.aggregator import WindowAggregator
from processor.consumer import create_kafka_consumer
from processor.retention import sweep
from processor.runner import run_iteration
from processor.speed_tracker import SpeedTracker
from processor.writer import BatchedRawWriter
from shared.config import settings
from shared.db import SessionLocal, engine
from shared.logging_setup import configure_logging
from shared.models import Base

configure_logging("b2-processor")
logger = logging.getLogger("processor")


def main() -> None:
    logger.info("processor_starting")

    Base.metadata.create_all(bind=engine)
    logger.info("db_tables_verified")

    start_http_server(settings.processor_metrics_port)
    logger.info("prometheus_started port=%d", settings.processor_metrics_port)

    consumer = create_kafka_consumer()
    aggregator = WindowAggregator()
    raw_writer = BatchedRawWriter()
    tracker = SpeedTracker()

    last_sweep_ts = 0.0

    logger.info("processor_ready")

    try:
        while True:
            run_iteration(consumer, aggregator, raw_writer, tracker)

            now = time.time()
            if now - last_sweep_ts >= settings.retention_sweep_interval_seconds:
                sweep(SessionLocal)
                last_sweep_ts = now

    except KeyboardInterrupt:
        logger.info("processor_shutting_down")
    finally:
        try:
            raw_writer.flush()
        except Exception:
            logger.exception("final_raw_flush_failed")
        consumer.close()
        logger.info("consumer_closed")


if __name__ == "__main__":
    main()
