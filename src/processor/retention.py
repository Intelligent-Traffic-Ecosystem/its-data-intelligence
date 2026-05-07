"""Time-based retention sweeper.

Per the B2 SRS section 7: traffic_events retained 24h, traffic_metrics 30d.
Before deleting old traffic_metrics, the sweeper archives them as parquet files
partitioned by camera_id and date for long-term analytics.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from shared.config import settings

logger = logging.getLogger(__name__)


def _metric_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "camera_id": row["camera_id"],
        "window_start": row["window_start"],
        "window_end": row["window_end"],
        "lane_id": row["lane_id"],
        "vehicle_count": row["vehicle_count"],
        "counts_by_class": row["counts_by_class"],
        "avg_speed_kmh": row["avg_speed_kmh"],
        "stopped_ratio": row["stopped_ratio"],
        "queue_length": row["queue_length"],
        "congestion_level": row["congestion_level"],
        "congestion_score": row["congestion_score"],
    }


def _archive_metrics(rows: list[Any], archive_path: str) -> int:
    """Archive metric rows as parquet partitioned by camera_id/date.

    Returns the number of rows written.
    """
    if not rows:
        return 0

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in rows:
        window_start = row["window_start"]
        if isinstance(window_start, str):
            window_start = datetime.fromisoformat(window_start)

        date_part = window_start.date().isoformat()
        key = (row["camera_id"], date_part)
        grouped.setdefault(key, []).append(_metric_row_to_dict(row))

    root = Path(archive_path)

    for (camera_id, date_part), records in grouped.items():
        partition_dir = root / f"camera_id={camera_id}" / f"date={date_part}"
        partition_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        file_name = f"traffic_metrics_{timestamp}.parquet"

        table = pa.Table.from_pylist(records)
        pq.write_table(table, partition_dir / file_name)

    return len(rows)


def sweep(
    session_factory: sessionmaker,
    events_retention_hours: int | None = None,
    metrics_retention_days: int | None = None,
    archive_enabled: bool | None = None,
    archive_path: str | None = None,
) -> tuple[int, int]:
    """Archive and delete rows older than retention windows.

    Returns (events_deleted, metrics_deleted).
    """
    events_hours = events_retention_hours or settings.retention_events_hours
    metrics_days = metrics_retention_days or settings.retention_metrics_days
    should_archive = (
        settings.metrics_archive_enabled if archive_enabled is None else archive_enabled
    )
    metrics_archive_path = archive_path or settings.metrics_archive_path

    session = session_factory()
    try:
        now = datetime.now(timezone.utc)
        events_cutoff = now - timedelta(hours=events_hours)
        metrics_cutoff = now - timedelta(days=metrics_days)

        old_metrics = (
            session.execute(
                text(
                    """
                    SELECT id, camera_id, window_start, window_end, lane_id,
                           vehicle_count, counts_by_class, avg_speed_kmh,
                           stopped_ratio, queue_length, congestion_level,
                           congestion_score
                    FROM traffic_metrics
                    WHERE window_start < :cutoff
                    ORDER BY camera_id, window_start
                    """
                ),
                {"cutoff": metrics_cutoff},
            )
            .mappings()
            .all()
        )

        if should_archive:
            archived_count = _archive_metrics(old_metrics, metrics_archive_path)
            logger.info("metrics_archived rows=%d path=%s", archived_count, metrics_archive_path)

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
