import json
import logging
import time
from typing import Iterable

from sqlalchemy import insert as sa_insert

from shared.config import settings
from shared.db import SessionLocal, engine
from shared.models import TrafficEvent, TrafficMetric
from shared.schemas import TrafficEventInput

logger = logging.getLogger(__name__)


def _is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def write_metric(metric: dict) -> None:
    """Insert or update an aggregated metric row.

    Camera-wide rows have lane_id=None; per-lane rows carry the lane_id. The
    unique index covers (camera_id, COALESCE(lane_id, -1), window_start) so a
    camera-wide row and per-lane rows for the same window coexist.
    """
    row = {
        "camera_id": metric["camera_id"],
        "window_start": metric["window_start"],
        "window_end": metric["window_end"],
        "lane_id": metric.get("lane_id"),
        "vehicle_count": metric["vehicle_count"],
        "counts_by_class": json.dumps(metric["counts_by_class"]),
        "avg_speed_kmh": metric["avg_speed_kmh"],
        "stopped_ratio": metric["stopped_ratio"],
        "queue_length": metric["queue_length"],
        "congestion_level": metric["congestion_level"],
        "congestion_score": metric["congestion_score"],
    }

    session = SessionLocal()
    try:
        if _is_postgres():
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(TrafficMetric).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=["camera_id", "lane_id", "window_start"],
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
            session.execute(stmt)
        else:
            existing = (
                session.query(TrafficMetric)
                .filter_by(
                    camera_id=row["camera_id"],
                    lane_id=row["lane_id"],
                    window_start=row["window_start"],
                )
                .one_or_none()
            )
            if existing is None:
                session.execute(sa_insert(TrafficMetric).values(**row))
            else:
                for k, v in row.items():
                    setattr(existing, k, v)

        session.commit()
        logger.info(
            "metric_written camera=%s lane=%s window=%s congestion=%s",
            metric["camera_id"],
            metric.get("lane_id"),
            metric["window_start"],
            metric["congestion_level"],
        )
    except Exception:
        session.rollback()
        logger.exception("Failed to write metric for camera %s", metric["camera_id"])
    finally:
        session.close()


def _event_to_row(event: TrafficEventInput) -> dict:
    return {
        "camera_id": event.camera_id,
        "ts": event.timestamp,
        "vehicle_id": event.vehicle_id,
        "vehicle_class": event.vehicle_class,
        "centroid_x": float(event.centroid.x),
        "centroid_y": float(event.centroid.y),
        "speed_kmh": event.speed_estimate,
        "frame_id": event.frame_id,
        "confidence": event.confidence,
        "bbox_x": int(event.bbox.x),
        "bbox_y": int(event.bbox.y),
        "bbox_w": int(event.bbox.w),
        "bbox_h": int(event.bbox.h),
        "lane_id": event.lane_id,
    }


def write_raw_events(events: Iterable[TrafficEventInput]) -> int:
    """Bulk-insert validated B1 events into traffic_events.

    Drop-and-log on failure (FR-2 tolerance — never crash the consumer).
    Returns the number of rows successfully inserted.
    """
    rows = [_event_to_row(e) for e in events]
    if not rows:
        return 0

    session = SessionLocal()
    try:
        session.execute(sa_insert(TrafficEvent), rows)
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        logger.exception("raw_event_batch_insert_failed count=%d", len(rows))
        return 0
    finally:
        session.close()


class BatchedRawWriter:
    """Buffers validated events; flushes on size or interval."""

    def __init__(
        self,
        batch_size: int | None = None,
        flush_interval: float | None = None,
    ) -> None:
        self.batch_size = batch_size or settings.raw_writer_batch_size
        self.flush_interval = flush_interval or settings.raw_writer_flush_interval_seconds
        self._buffer: list[TrafficEventInput] = []
        self._first_buffered_at: float | None = None

    def add(self, event: TrafficEventInput) -> None:
        if not self._buffer:
            self._first_buffered_at = time.time()
        self._buffer.append(event)
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush_due(self) -> None:
        if not self._buffer or self._first_buffered_at is None:
            return
        if time.time() - self._first_buffered_at >= self.flush_interval:
            self.flush()

    def flush(self) -> int:
        if not self._buffer:
            return 0
        events = self._buffer
        self._buffer = []
        self._first_buffered_at = None
        return write_raw_events(events)
