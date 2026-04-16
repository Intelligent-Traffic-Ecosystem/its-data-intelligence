from fastapi import APIRouter
from sqlalchemy import text

from shared.db import SessionLocal
from shared.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check():
    """Liveness probe for B4 monitoring."""
    pg_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception:
        pg_status = "unreachable"

    # Kafka health is checked by the processor, not the API.
    # Report as "not_checked" since the API doesn't consume from Kafka directly.
    return HealthResponse(status="ok", kafka="not_checked", postgres=pg_status)
