import logging

from fastapi import APIRouter
from sqlalchemy import text

from shared.config import settings
from shared.db import SessionLocal
from shared.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_kafka_admin = None


def _check_kafka() -> str:
    """Probe Kafka via a cached AdminClient. Returns 'ok' or 'unreachable'."""
    global _kafka_admin
    try:
        if _kafka_admin is None:
            from kafka import KafkaAdminClient

            _kafka_admin = KafkaAdminClient(
                bootstrap_servers=settings.kafka_brokers.split(","),
                request_timeout_ms=2000,
                client_id="b2-api-health",
            )
        _kafka_admin.list_topics()
        return "ok"
    except Exception:
        logger.warning("kafka_health_check_failed", exc_info=True)
        _kafka_admin = None  # force re-creation on next probe
        return "unreachable"


@router.get("/health", response_model=HealthResponse)
def health_check():
    """Liveness probe for B4. Checks both Postgres and Kafka."""
    pg_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception:
        pg_status = "unreachable"

    kafka_status = _check_kafka()

    overall = "ok" if pg_status == "ok" and kafka_status == "ok" else "degraded"
    return HealthResponse(status=overall, kafka=kafka_status, postgres=pg_status)
