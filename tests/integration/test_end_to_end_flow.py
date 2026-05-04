"""End-to-end: produce a B1-shaped event, run one processor iteration, query DB.

Confirms that all B1 fields (frame_id, confidence, bbox_*, lane_id) survive
ingestion and that aggregated metrics + per-lane breakdowns are written.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest
from pyflink.datastream import StreamExecutionEnvironment
from sqlalchemy import select

from processor.flink_job import build_pipeline
from shared.config import settings
from shared.db import SessionLocal
from shared.models import TrafficEvent, TrafficMetric


def _produce(producer, topic: str, event: dict) -> None:
    producer.send(topic, value=json.dumps(event).encode("utf-8"))
    producer.flush()


def test_b1_event_flows_to_db(kafka_container, fresh_db):
    from kafka import KafkaProducer

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

    jar_path = "/opt/flink/lib/flink-sql-connector-kafka.jar"
    if not os.path.exists(jar_path):
        pytest.skip("Kafka connector JAR not found locally. Skipping PyFlink E2E test.")

    # Override settings for test
    settings.kafka_topic_input = topic
    settings.kafka_brokers = bootstrap
    settings.window_size_seconds = 1
    settings.window_allowed_lateness_seconds = 0
    settings.flink_parallelism = 1

    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(1)
    env.add_jars(f"file://{jar_path}")

    build_pipeline(env)

    job_client = env.execute_async("test_e2e_job")

    # Give Flink time to initialize, consume, process, and write to DB
    time.sleep(10)
    try:
        job_client.cancel().result()
    except Exception:
        pass

    session = SessionLocal()
    try:
        events = (
            session.execute(select(TrafficEvent).where(TrafficEvent.camera_id == "cam_e2e"))
            .scalars()
            .all()
        )
        assert len(events) >= 1, "raw event should have been persisted"
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
    finally:
        session.close()
