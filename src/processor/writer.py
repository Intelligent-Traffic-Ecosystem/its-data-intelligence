import json
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert

from shared.db import SessionLocal
from shared.models import TrafficMetric

logger = logging.getLogger(__name__)


def write_metric(metric: dict) -> None:
    """Insert or update an aggregated metric row in PostgreSQL.

    Uses upsert (ON CONFLICT DO UPDATE) on (camera_id, window_start)
    so reprocessed windows overwrite cleanly.
    """
    row = {
        "camera_id": metric["camera_id"],
        "window_start": metric["window_start"],
        "window_end": metric["window_end"],
        "vehicle_count": metric["vehicle_count"],
        "counts_by_class": json.dumps(metric["counts_by_class"]),
        "avg_speed_kmh": metric["avg_speed_kmh"],
        "stopped_ratio": metric["stopped_ratio"],
        "queue_length": metric["queue_length"],
        "congestion_level": metric["congestion_level"],
        "congestion_score": metric["congestion_score"],
    }

    stmt = pg_insert(TrafficMetric).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["camera_id", "window_start"],
        set_={
            "window_end": stmt.excluded.window_end,
            "vehicle_count": stmt.excluded.vehicle_count,
            "counts_by_class": stmt.excluded.counts_by_class,
            "avg_speed_kmh": stmt.excluded.avg_speed_kmh,
            "stopped_ratio": stmt.excluded.stopped_ratio,
            "queue_length": stmt.excluded.queue_length,
            "congestion_level": stmt.excluded.congestion_level,
            "congestion_score": stmt.excluded.congestion_score,
        },
    )

    session = SessionLocal()
    try:
        session.execute(stmt)
        session.commit()
        logger.info(
            "Wrote metric: camera=%s window=%s congestion=%s",
            metric["camera_id"],
            metric["window_start"],
            metric["congestion_level"],
        )
    except Exception:
        session.rollback()
        logger.exception("Failed to write metric for camera %s", metric["camera_id"])
    finally:
        session.close()
