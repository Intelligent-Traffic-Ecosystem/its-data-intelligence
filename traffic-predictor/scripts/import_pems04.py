"""Import a PEMS04 subset into the ``traffic_metrics`` table for ST-GCN training.

PEMS04 is a real California-freeway loop-detector dataset of shape
``[16992 timesteps, 307 sensors, 3 features]`` where the three features are
``[flow, occupancy, speed]`` sampled every 5 minutes for ~59 days.

We map a connected subgraph of 12 sensors onto a 3-camera × 4-lane topology
and write rows into the same ``traffic_metrics`` table the live aggregator
uses. The ST-GCN trainer then reads them via ``TrafficMetricsLoader`` exactly
like it would read live traffic.

Usage (from the ``traffic-predictor/`` directory):

    source .venv/bin/activate
    STGCN_DATABASE__URL="sqlite:///./data/training.db" \\
        python scripts/import_pems04.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import (
    BigInteger,
    Column,
    Integer as SAInteger,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    insert,
)

ROOT = Path(__file__).resolve().parent.parent
NPZ_PATH = ROOT / "data" / "PEMS04.npz"
EDGES_PATH = ROOT / "data" / "PEMS04_distance.csv"

PEMS_INTERVAL = timedelta(minutes=5)
# PEMS04 has 16992 timesteps × 5 min = 59 days. We anchor the import so the
# entire range falls inside the trainer's default 30-day lookback window.
# (16992 × 5 min ≈ 59 days; 60 days back covers it with margin.)
PEMS_START = (
    datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    - timedelta(days=60)
)

# Fixed topology chosen from BFS over PEMS04 adjacency starting at sensor 0.
# Each row is one camera; the 4 sensors become lane_id 1..4.
CAMERAS: list[tuple[str, list[int]]] = [
    ("cam-001", [0, 92, 243, 62]),
    ("cam-002", [28, 57, 242, 68]),
    ("cam-003", [117, 173, 170, 97]),
]

# Congestion classifier matches src/processor/congestion.py defaults.
WEIGHT_COUNT = 0.4
WEIGHT_SPEED = 0.4
WEIGHT_STOPPED = 0.2
THRESHOLD_LOW = 0.30
THRESHOLD_MODERATE = 0.55
THRESHOLD_HIGH = 0.80
MAX_VEHICLE_COUNT = 700  # PEMS04 flow saturates well above the 50 used at runtime.
MAX_SPEED_KMH = 110.0


logger = logging.getLogger("import_pems04")


def _build_table(metadata: MetaData) -> Table:
    """Mirror of ``shared.models.TrafficMetric`` so we don't depend on src/."""
    return Table(
        "traffic_metrics",
        metadata,
        # SQLite autoincrement requires plain INTEGER PK; BigInteger is fine
        # on Postgres because the live migration uses BIGSERIAL.
        Column(
            "id",
            SAInteger().with_variant(BigInteger(), "postgresql"),
            primary_key=True,
            autoincrement=True,
        ),
        Column("camera_id", Text, nullable=False),
        Column("window_start", DateTime(timezone=True), nullable=False),
        Column("window_end", DateTime(timezone=True), nullable=False),
        Column("lane_id", Integer),
        Column("vehicle_count", Integer),
        Column("counts_by_class", Text),
        Column("avg_speed_kmh", Float),
        Column("stopped_ratio", Float),
        Column("queue_length", Integer),
        Column("congestion_level", Text),
        Column("congestion_score", Float),
        UniqueConstraint(
            "camera_id", "lane_id", "window_start",
            name="uq_metrics_camera_lane_window",
        ),
        Index("ix_traffic_metrics_camera_window", "camera_id", "window_start"),
    )


def _classify(score: float) -> str:
    if score < THRESHOLD_LOW:
        return "LOW"
    if score < THRESHOLD_MODERATE:
        return "MODERATE"
    if score < THRESHOLD_HIGH:
        return "HIGH"
    return "SEVERE"


def _normalize(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return max(0.0, min(value / max_value, 1.0))


def _row(
    camera_id: str,
    lane_id: int,
    window_start: datetime,
    flow: float,
    occupancy: float,
    speed: float,
) -> dict:
    """Map (flow, occupancy, speed) to a traffic_metrics row."""
    vehicle_count = int(round(max(flow, 0.0)))
    avg_speed_kmh = round(max(speed, 0.0), 2)
    stopped_ratio = round(max(0.0, min(occupancy, 1.0)), 4)

    score = (
        WEIGHT_COUNT * _normalize(vehicle_count, MAX_VEHICLE_COUNT)
        + WEIGHT_SPEED * (1.0 - _normalize(avg_speed_kmh, MAX_SPEED_KMH))
        + WEIGHT_STOPPED * stopped_ratio
    )
    score = round(max(0.0, min(score, 1.0)), 4)

    return {
        "camera_id": camera_id,
        "window_start": window_start,
        "window_end": window_start + PEMS_INTERVAL,
        "lane_id": lane_id,
        "vehicle_count": vehicle_count,
        "counts_by_class": '{"car": %d}' % vehicle_count,
        "avg_speed_kmh": avg_speed_kmh,
        "stopped_ratio": stopped_ratio,
        "queue_length": int(stopped_ratio * 30),  # rough proxy from occupancy
        "congestion_level": _classify(score),
        "congestion_score": score,
    }


def _emit_topology_summary() -> None:
    if not EDGES_PATH.exists():
        logger.warning("No PEMS04_distance.csv at %s; skipping edge dump", EDGES_PATH)
        return

    selected: dict[int, tuple[str, int]] = {}
    for cam, sensors in CAMERAS:
        for lane_idx, sensor in enumerate(sensors, start=1):
            selected[sensor] = (cam, lane_idx)

    intra: list[tuple[str, int, int]] = []
    cross: list[tuple[str, int, str, int]] = []
    with open(EDGES_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            a, b = int(row["from"]), int(row["to"])
            if a not in selected or b not in selected:
                continue
            (cam_a, lane_a), (cam_b, lane_b) = selected[a], selected[b]
            if cam_a == cam_b:
                intra.append((cam_a, lane_a, lane_b))
            else:
                cross.append((cam_a, lane_a, cam_b, lane_b))

    logger.info("Intra-camera adjacencies (informational): %s", intra)
    logger.info("Cross-camera adjacencies (paste into config): %s", cross)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-url",
        default=os.environ.get("STGCN_DATABASE__URL", "sqlite:///./data/training.db"),
        help="SQLAlchemy DB URL (default: sqlite:///./data/training.db)",
    )
    parser.add_argument(
        "--limit-timesteps",
        type=int,
        default=None,
        help="Limit to first N PEMS04 timesteps for quick smoke-test runs.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Delete existing traffic_metrics rows before importing.",
    )
    args = parser.parse_args()

    if not NPZ_PATH.exists():
        logger.error(
            "PEMS04.npz not found at %s — download with: "
            "curl -sL -o data/PEMS04.npz "
            "'https://raw.githubusercontent.com/guoshnBJTU/ASTGNN/main/data/PEMS04/PEMS04.npz'",
            NPZ_PATH,
        )
        return 1

    logger.info("loading %s", NPZ_PATH)
    arr = np.load(NPZ_PATH)["data"]  # [T, N, 3]
    if args.limit_timesteps:
        arr = arr[: args.limit_timesteps]
    timesteps = arr.shape[0]
    logger.info("dataset shape=%s (timesteps=%d, sensors=%d)",
                arr.shape, timesteps, arr.shape[1])

    engine = create_engine(args.db_url, future=True)
    metadata = MetaData()
    table = _build_table(metadata)
    metadata.create_all(engine)
    logger.info("traffic_metrics table ready at %s", args.db_url)

    if args.truncate:
        with engine.begin() as conn:
            conn.execute(delete(table))
        logger.info("truncated traffic_metrics")

    rows: list[dict] = []
    for t in range(timesteps):
        ts = PEMS_START + t * PEMS_INTERVAL
        for camera_id, sensor_ids in CAMERAS:
            for lane_idx, sensor in enumerate(sensor_ids, start=1):
                flow, occupancy, speed = arr[t, sensor]
                rows.append(_row(camera_id, lane_idx, ts, flow, occupancy, speed))

        if len(rows) >= 5000:
            with engine.begin() as conn:
                conn.execute(insert(table), rows)
            rows.clear()

    if rows:
        with engine.begin() as conn:
            conn.execute(insert(table), rows)

    n_cameras = len(CAMERAS)
    n_lanes_per_cam = len(CAMERAS[0][1])
    logger.info(
        "imported %d rows (%d timesteps × %d cameras × %d lanes)",
        timesteps * n_cameras * n_lanes_per_cam,
        timesteps,
        n_cameras,
        n_lanes_per_cam,
    )
    _emit_topology_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
