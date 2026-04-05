"""B2 Stream Processor — entry point.

Consumes traffic events from Kafka, aggregates them in time windows,
computes metrics, classifies congestion, and writes results to PostgreSQL.

Run: python -m processor.main
"""

import logging
import time

from shared.models import Base
from shared.db import engine
from processor.consumer import create_kafka_consumer
from processor.validator import validate_event
from processor.aggregator import WindowAggregator

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("processor")


def main() -> None:
    logger.info("Starting B2 stream processor...")

    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified")

    consumer = create_kafka_consumer()
    aggregator = WindowAggregator()

    logger.info("Processing events — press Ctrl+C to stop")

    try:
        while True:
            # Poll Kafka (timeout 1 second)
            records = consumer.poll(timeout_ms=1000)

            for topic_partition, messages in records.items():
                for message in messages:
                    event = validate_event(message.value)
                    if event:
                        aggregator.add_event(event)

            # Flush any expired windows
            aggregator.flush_expired()

            # Small sleep to avoid busy-spinning when no messages
            if not records:
                time.sleep(0.1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        consumer.close()
        logger.info("Consumer closed")


if __name__ == "__main__":
    main()
