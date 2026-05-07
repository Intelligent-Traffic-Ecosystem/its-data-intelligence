"""Retention sweeper deletes rows beyond the SRS retention windows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow.parquet as pq


def test_sweep_deletes_old_rows(fresh_db, tmp_path):
    from sqlalchemy import insert, select

    from processor.retention import sweep
    from shared.db import SessionLocal
    from shared.models import TrafficEvent, TrafficMetric

    now = datetime.now(timezone.utc)

    session = SessionLocal()
    try:
        session.execute(
            insert(TrafficEvent),
            [
                {"camera_id": "cam_R", "ts": now - timedelta(hours=48), "vehicle_class": "car"},
                {"camera_id": "cam_R", "ts": now - timedelta(hours=1), "vehicle_class": "car"},
            ],
        )
        session.execute(
            insert(TrafficMetric),
            [
                {
                    "camera_id": "cam_R",
                    "window_start": now - timedelta(days=45),
                    "window_end": now - timedelta(days=45) + timedelta(seconds=5),
                    "vehicle_count": 0,
                    "avg_speed_kmh": 0,
                    "stopped_ratio": 0,
                    "queue_length": 0,
                    "congestion_level": "LOW",
                    "congestion_score": 0,
                },
                {
                    "camera_id": "cam_R",
                    "window_start": now - timedelta(days=1),
                    "window_end": now - timedelta(days=1) + timedelta(seconds=5),
                    "vehicle_count": 0,
                    "avg_speed_kmh": 0,
                    "stopped_ratio": 0,
                    "queue_length": 0,
                    "congestion_level": "LOW",
                    "congestion_score": 0,
                },
            ],
        )
        session.commit()
    finally:
        session.close()

    events_deleted, metrics_deleted = sweep(
        SessionLocal, archive_enabled=True, archive_path=str(tmp_path)
    )
    assert events_deleted >= 1
    assert metrics_deleted >= 1

    archive_files = list(tmp_path.rglob("*.parquet"))
    assert archive_files

    assert any("camera_id=cam_R" in str(path) for path in archive_files)
    assert any("date=" in str(path) for path in archive_files)

    table = pq.ParquetFile(archive_files[0]).read()
    archived = table.to_pylist()

    assert len(archived) >= 1
    assert archived[0]["camera_id"] == "cam_R"
    assert archived[0]["congestion_level"] == "LOW"

    session = SessionLocal()
    try:
        remaining_events = (
            session.execute(select(TrafficEvent).where(TrafficEvent.camera_id == "cam_R"))
            .scalars()
            .all()
        )
        remaining_metrics = (
            session.execute(select(TrafficMetric).where(TrafficMetric.camera_id == "cam_R"))
            .scalars()
            .all()
        )
        assert all(e.ts >= now - timedelta(hours=24) for e in remaining_events)
        assert all(m.window_start >= now - timedelta(days=30) for m in remaining_metrics)
    finally:
        session.close()
