"""Helper to load admin thresholds from database into settings."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import settings
from shared.models import AdminThreshold

logger = logging.getLogger(__name__)


def load_thresholds_from_db(db: Session) -> None:
    """Load congestion thresholds from admin_thresholds table into settings.

    If no thresholds exist in the database, the current settings values are preserved.
    This function should be called at application startup to ensure the processor
    and API use database-configured thresholds.

    The query selects the first row ordered by ID. In normal operation, only one
    threshold record should exist (created via the admin API). If multiple records
    exist due to manual DB operations, the lowest ID is used for consistency.
    """
    try:
        row = (
            db.execute(select(AdminThreshold).order_by(AdminThreshold.id.asc()).limit(1))
            .scalar_one_or_none()
        )
        if row is not None:
            settings.congestion_threshold_low = row.congestion_threshold_low
            settings.congestion_threshold_moderate = row.congestion_threshold_moderate
            settings.congestion_threshold_high = row.congestion_threshold_high
            logger.info(
                "thresholds_loaded_from_db",
                extra={
                    "low": row.congestion_threshold_low,
                    "moderate": row.congestion_threshold_moderate,
                    "high": row.congestion_threshold_high,
                },
            )
        else:
            logger.info("no_thresholds_in_db_using_defaults")
    except Exception:
        logger.exception(
            "failed_to_load_thresholds_from_db_using_defaults",
            extra={
                "low": settings.congestion_threshold_low,
                "moderate": settings.congestion_threshold_moderate,
                "high": settings.congestion_threshold_high,
            },
        )
