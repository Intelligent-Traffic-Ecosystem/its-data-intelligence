"""B2 Stream Processor — entry point.

Consumes traffic events from Kafka, aggregates them in time windows,
computes metrics, classifies congestion, persists raw events + metrics to
PostgreSQL, and periodically prunes data per the SRS retention policy.

Run: python -m processor.main
"""

import logging
import os
import threading
import time

from prometheus_client import start_http_server
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.checkpointing_mode import CheckpointingMode

from processor.flink_job import build_pipeline
from processor.retention import sweep
from shared.config import settings
from shared.db import SessionLocal, engine
from shared.logging_setup import configure_logging
from shared.models import Base
from shared.threshold_loader import load_thresholds_from_db

configure_logging("b2-processor")
logger = logging.getLogger("processor")


def retention_loop():
    """Background thread to periodically sweep old data."""
    while True:
        try:
            sweep(SessionLocal)
        except Exception:
            logger.exception("retention_sweep_failed")
        time.sleep(settings.retention_sweep_interval_seconds)


def main() -> None:
    logger.info("processor_starting")

    Base.metadata.create_all(bind=engine)
    logger.info("db_tables_verified")

    # Load admin thresholds from database
    with SessionLocal() as db:
        load_thresholds_from_db(db)

    start_http_server(settings.processor_metrics_port)
    logger.info("prometheus_started port=%d", settings.processor_metrics_port)

    retention_thread = threading.Thread(target=retention_loop, daemon=True)
    retention_thread.start()
    logger.info("retention_sweep_thread_started")

    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(settings.flink_parallelism)
    env.enable_checkpointing(settings.flink_checkpoint_interval_ms, CheckpointingMode.EXACTLY_ONCE)

    jar_path = "/opt/flink/lib/flink-sql-connector-kafka.jar"
    if os.path.exists(jar_path):
        env.add_jars(f"file://{jar_path}")

    logger.info("flink_env_configured")

    build_pipeline(env)

    logger.info("processor_ready")

    try:
        env.execute("b2-stream-processor")
    except KeyboardInterrupt:
        logger.info("processor_shutting_down")
    except Exception:
        logger.exception("flink_execution_failed")
    finally:
        logger.info("processor_closed")


if __name__ == "__main__":
    main()
