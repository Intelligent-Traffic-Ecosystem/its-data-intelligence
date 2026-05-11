import logging

from kafka import KafkaConsumer

from shared.config import settings

logger = logging.getLogger(__name__)


def create_kafka_consumer() -> KafkaConsumer:
    """Create a Kafka consumer for the traffic events topic."""
    logger.info(
        "Connecting to Kafka at %s, topic: %s",
        settings.kafka_brokers,
        settings.kafka_topic_input,
    )

    consumer = KafkaConsumer(
        settings.kafka_topic_input,
        bootstrap_servers=settings.kafka_brokers.split(","),
        group_id="b2-stream-processor",
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: v.decode("utf-8"),
    )

    logger.info("Kafka consumer connected")
    return consumer
