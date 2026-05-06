"""
Database loader — fetches time-series records from the ``traffic_metrics`` table
and returns them as a tidy :class:`pandas.DataFrame` suitable for graph dataset
construction.

The loader queries **per-lane** rows (``lane_id IS NOT NULL``) and handles
missing windows by forward-filling so that every (camera, lane) node always
has a complete, evenly-spaced sequence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.config import Config, get_config

logger = logging.getLogger(__name__)

# Columns retrieved from traffic_metrics
_METRICS_COLS = (
    "camera_id",
    "lane_id",
    "window_start",
    "vehicle_count",
    "avg_speed_kmh",
    "stopped_ratio",
    "congestion_score",
    "congestion_level",
)

_CONGESTION_LEVEL_MAP: dict[str | None, int] = {
    "LOW": 0,
    "MODERATE": 1,
    "HIGH": 2,
    "SEVERE": 2,  # fold SEVERE → HIGH for the 3-class output head
    None: 0,
}


class TrafficMetricsLoader:
    """
    Fetches and preprocesses traffic metrics from PostgreSQL.

    Parameters
    ----------
    config:
        Application config. Defaults to the global singleton.
    engine:
        Optional pre-built SQLAlchemy engine (useful for testing with SQLite).
    """

    def __init__(
        self,
        config: Config | None = None,
        engine: Engine | None = None,
    ) -> None:
        self._cfg = config or get_config()
        self._engine: Engine = engine or create_engine(
            self._cfg.database.url,
            pool_size=self._cfg.database.pool_size,
            max_overflow=self._cfg.database.max_overflow,
            pool_pre_ping=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_training_data(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """
        Load the full historical dataset for model training.

        Returns a DataFrame indexed by ``(camera_id, lane_id, window_start)``
        with all feature columns and a ``congestion_level_idx`` integer label.
        """
        end = end or datetime.now(tz=timezone.utc)
        start = start or (end - timedelta(days=30))
        logger.info("Loading training data from %s to %s", start, end)
        df = self._query(start, end)
        df = self._fill_gaps(df)
        df = self._engineer_features(df)
        logger.info("Training data shape: %s", df.shape)
        return df

    def load_recent(self, lookback_minutes: int | None = None) -> pd.DataFrame:
        """
        Load the most recent N minutes of metrics for real-time inference.

        The returned DataFrame is sorted by ``window_start`` ascending so that
        the last ``input_window`` rows form the current model input.
        """
        minutes = lookback_minutes or self._cfg.inference.db_lookback_minutes
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(minutes=minutes)
        df = self._query(start, end)
        df = self._fill_gaps(df)
        df = self._engineer_features(df)
        return df

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query(self, start: datetime, end: datetime) -> pd.DataFrame:
        sql = text(
            f"""
            SELECT {', '.join(_METRICS_COLS)}
            FROM   traffic_metrics
            WHERE  lane_id IS NOT NULL
              AND  window_start >= :start
              AND  window_start <  :end
            ORDER  BY camera_id, lane_id, window_start
            """
        )
        with self._engine.connect() as conn:
            result = conn.execute(sql, {"start": start, "end": end})
            rows: list[dict[str, Any]] = [dict(r._mapping) for r in result]

        if not rows:
            logger.warning("No metrics rows returned for [%s, %s)", start, end)
            return pd.DataFrame(columns=list(_METRICS_COLS))

        df = pd.DataFrame(rows)
        df["window_start"] = pd.to_datetime(df["window_start"], utc=True)
        df["lane_id"] = df["lane_id"].astype(int)
        return df

    def _fill_gaps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure a regular 5-second grid for every (camera_id, lane_id) pair.

        Missing windows are forward-filled (last-observation-carried-forward).
        """
        if df.empty:
            return df

        step = timedelta(seconds=self._cfg.features.window_size_seconds)
        pieces: list[pd.DataFrame] = []

        for (cam, lane), grp in df.groupby(["camera_id", "lane_id"], sort=False):
            grp = grp.set_index("window_start").sort_index()
            full_idx = pd.date_range(
                start=grp.index.min(),
                end=grp.index.max(),
                freq=step,
                tz="UTC",
            )
            grp = grp.reindex(full_idx)
            grp["camera_id"] = cam
            grp["lane_id"] = lane
            grp = grp.ffill()
            grp.index.name = "window_start"
            pieces.append(grp.reset_index())

        return pd.concat(pieces, ignore_index=True)

    def _engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add time-encoding columns and normalise numeric features."""
        norm = self._cfg.features.normalization

        df["vehicle_count"] = (
            df["vehicle_count"].fillna(0).clip(0, norm.vehicle_count_max)
            / norm.vehicle_count_max
        )
        df["avg_speed_kmh"] = (
            df["avg_speed_kmh"].fillna(0).clip(0, norm.speed_max_kmh)
            / norm.speed_max_kmh
        )
        df["stopped_ratio"] = df["stopped_ratio"].fillna(0).clip(0, 1)
        df["congestion_score"] = df["congestion_score"].fillna(0).clip(0, 1)

        # Cyclical time encodings
        hour = df["window_start"].dt.hour + df["window_start"].dt.minute / 60
        dow = df["window_start"].dt.dayofweek
        df["hour_sin"] = (2 * 3.14159265 * hour / 24).apply(__import__("math").sin)
        df["hour_cos"] = (2 * 3.14159265 * hour / 24).apply(__import__("math").cos)
        df["dow_sin"] = (2 * 3.14159265 * dow / 7).apply(__import__("math").sin)
        df["dow_cos"] = (2 * 3.14159265 * dow / 7).apply(__import__("math").cos)

        df["congestion_level_idx"] = (
            df["congestion_level"].map(_CONGESTION_LEVEL_MAP).fillna(0).astype(int)
        )

        return df
