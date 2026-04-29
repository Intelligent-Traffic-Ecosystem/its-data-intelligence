"""Time-based retention sweeper.

Per the B2 SRS section 7: traffic_events retained 24h, traffic_metrics 30d.
A lightweight Python sweeper runs inside the processor on a configurable
interval (default 1h) so we avoid a pg_cron extension dependency.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from shared.config import settings

logger = logging.getLogger(__name__)


def sweep(
    session_factory: sessionmaker,
    events_retention_hours: int | None = None,
    metrics_retention_days: int | None = None,
) -> tuple[int, int]:
    """Delete rows older than retention windows. Returns (events_deleted, metrics_deleted)."""
    events_hours = events_retention_hours or settings.retention_events_hours
    metrics_days = metrics_retention_days or settings.retention_metrics_days

    session = session_factory()
    try:
        # Use parameterised SQL so SQLite tests can also exercise this path
        # (SQLite supports `now() - interval` only via datetime() functions; we
        # build the cutoff in Python to stay portable).
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        events_cutoff = now - timedelta(hours=events_hours)
        metrics_cutoff = now - timedelta(days=metrics_days)

        ev_result = session.execute(
            text("DELETE FROM traffic_events WHERE ts < :cutoff"),
            {"cutoff": events_cutoff},
        )
        met_result = session.execute(
            text("DELETE FROM traffic_metrics WHERE window_start < :cutoff"),
            {"cutoff": metrics_cutoff},
        )
        session.commit()
        events_deleted = ev_result.rowcount or 0
        metrics_deleted = met_result.rowcount or 0
        logger.info(
            "retention_sweep events_deleted=%d metrics_deleted=%d",
            events_deleted,
            metrics_deleted,
        )
        return events_deleted, metrics_deleted
    except Exception:
        session.rollback()
        logger.exception("retention_sweep_failed")
        return 0, 0
    finally:
        session.close()
