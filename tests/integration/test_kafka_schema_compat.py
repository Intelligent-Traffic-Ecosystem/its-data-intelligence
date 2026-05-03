"""Verify the mock producer's output is accepted verbatim by the B2 validator.

This guards the B1 ↔ B2 wire contract: every B1-shaped event the producer
emits must round-trip through Kafka and pass ``validate_event`` unchanged.
"""

from __future__ import annotations

import json
import sys

import pytest


@pytest.mark.usefixtures("kafka_container")
def test_mock_producer_events_are_valid(kafka_container):
    """Generate 100 events, push to Kafka, consume, validate every one."""
    bootstrap = kafka_container.get_bootstrap_server()

    sys.path.insert(0, "tools")
    from kafka import KafkaConsumer, KafkaProducer
    from mock_producer import generate_event  # noqa: E402

    from processor.validator import validate_event

    topic = "traffic.events.raw.compat"
    producer = KafkaProducer(
        bootstrap_servers=[bootstrap],
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )

    sent = 0
    for i in range(100):
        event = generate_event(
            camera_id=f"cam_{i % 4:02d}",
            vehicle_counter=i,
            with_lane=(i % 5 == 0),
            include_speed=(i % 7 != 0),
        )
        producer.send(topic, value=event)
        sent += 1
    producer.flush()
    producer.close()

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=[bootstrap],
        auto_offset_reset="earliest",
        group_id="b2-compat-test",
        consumer_timeout_ms=10000,
        value_deserializer=lambda v: v.decode("utf-8"),
    )

    validated = 0
    for message in consumer:
        result = validate_event(message.value)
        assert result is not None, f"validator rejected: {message.value}"
        validated += 1
        if validated >= sent:
            break
    consumer.close()

    assert validated == sent, f"expected {sent} valid events, got {validated}"
