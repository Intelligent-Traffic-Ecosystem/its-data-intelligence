"""End-to-end: produce a B1-shaped event, run one processor iteration, query DB.

Confirms that all B1 fields (frame_id, confidence, bbox_*, lane_id) survive
ingestion and that aggregated metrics + per-lane breakdowns are written.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone


def _produce(producer, topic: str, event: dict) -> None:
    producer.send(topic, value=json.dumps(event).encode("utf-8"))
    producer.flush()


def test_b1_event_flows_to_db(kafka_container, fresh_db):
    from kafka import KafkaConsumer, KafkaProducer

    from processor.aggregator import WindowAggregator
    from processor.runner import run_iteration
    from processor.speed_tracker import SpeedTracker
    from processor.writer import BatchedRawWriter

    topic = "traffic.events.raw.e2e"
    bootstrap = kafka_container.get_bootstrap_server()

    producer = KafkaProducer(bootstrap_servers=[bootstrap])

    now = datetime.now(timezone.utc).replace(microsecond=0)
    event = {
        "camera_id": "cam_e2e",
        "timestamp": now.isoformat(),
        "frame_id": 42,
        "vehicle_id": "veh_e2e_1",
        "class": "car",
        "confidence": 0.91,
        "bbox": {"x": 100, "y": 200, "w": 80, "h": 50},
        "centroid": {"x": 140, "y": 225},
        "lane_id": 2,
        "speed_estimate": 30.0,
    }
    _produce(producer, topic, event)
    producer.close()

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=[bootstrap],
        auto_offset_reset="earliest",
        group_id="b2-e2e-test",
        consumer_timeout_ms=5000,
        value_deserializer=lambda v: v.decode("utf-8"),
    )

    aggregator = WindowAggregator(window_size=1)
    raw_writer = BatchedRawWriter(batch_size=1, flush_interval=0.1)
    tracker = SpeedTracker()

    deadline = time.time() + 10
    processed = 0
    while time.time() < deadline and processed == 0:
        processed += run_iteration(consumer, aggregator, raw_writer, tracker, poll_timeout_ms=500)

    raw_writer.flush()
    time.sleep(2)
    aggregator.flush_all()
    consumer.close()

    from sqlalchemy import select

    from shared.db import SessionLocal
    from shared.models import TrafficEvent, TrafficMetric

    session = SessionLocal()
    try:
        events = (
            session.execute(select(TrafficEvent).where(TrafficEvent.camera_id == "cam_e2e"))
            .scalars()
            .all()
        )
        assert len(events) == 1, "raw event should have been persisted"
        row = events[0]
        assert row.frame_id == 42
        assert row.confidence == 0.91
        assert (row.bbox_x, row.bbox_y, row.bbox_w, row.bbox_h) == (100, 200, 80, 50)
        assert row.lane_id == 2
        assert row.vehicle_class == "car"

        metrics = (
            session.execute(select(TrafficMetric).where(TrafficMetric.camera_id == "cam_e2e"))
            .scalars()
            .all()
        )
        assert len(metrics) >= 1, "at least one metric row should exist"
        levels = {(m.lane_id, m.vehicle_count) for m in metrics}
        assert any(lane_id is None for lane_id, _ in levels), "camera-wide row missing"
        assert any(lane_id == 2 for lane_id, _ in levels), "per-lane row missing"
    finally:
        session.close()
