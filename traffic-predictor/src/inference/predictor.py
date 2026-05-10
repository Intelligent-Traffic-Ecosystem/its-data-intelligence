"""
Real-time inference engine.

:class:`TrafficPredictor` is the single entry point for producing lane-level
forecasts.  It:

1. Fetches recent ``traffic_metrics`` rows from PostgreSQL via
   :class:`~src.data.db_loader.TrafficMetricsLoader`.
2. Aligns the data to the graph node ordering and builds the model input
   tensor ``x`` of shape ``[1, T_in, N, F]``.
3. Runs the ST-GCN model forward pass.
4. Decodes outputs into human-readable :class:`LaneForecast` objects.
5. Runs :class:`~src.inference.congestion_estimator.CongestionEstimator`
   to annotate each forecast with estimated start/end times.

The predictor is designed to be long-lived (loaded once, called repeatedly)
and is thread-safe in CPU mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import numpy as np
import torch
from torch import Tensor

from src.config import Config, get_config
from src.data.db_loader import TrafficMetricsLoader
from src.data.graph_builder import LaneGraph, build_graph
from src.inference.congestion_estimator import CongestionEstimator, CongestionWindow
from src.model.stgcn import STGCN

logger = logging.getLogger(__name__)

CongestionLabel = Literal["LOW", "MEDIUM", "HIGH"]
_IDX_TO_LABEL: dict[int, CongestionLabel] = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}


@dataclass
class LaneForecast:
    """
    Full forecasting output for a single (camera, lane) over the horizon.
    """

    camera_id: str
    lane_id: int

    # Predicted vehicle counts for each future step (normalised → raw)
    predicted_vehicle_counts: list[float]   # length = T_out

    # Congestion probability at each future step [0, 1]
    congestion_probabilities: list[float]   # length = T_out

    # Predicted congestion level at each future step
    congestion_levels: list[CongestionLabel]  # length = T_out

    # Estimated congestion episode
    congestion_start: datetime | None
    congestion_end: datetime | None
    peak_congestion_probability: float

    # Timestamp of when this forecast was generated
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    # Future timestamps (one per predicted step)
    forecast_timestamps: list[datetime] = field(default_factory=list)


class TrafficPredictor:
    """
    Lane-level real-time traffic forecaster.

    Parameters
    ----------
    config:
        Application config. Defaults to the global singleton.
    model:
        Pre-loaded :class:`~src.model.stgcn.STGCN` instance. If ``None``,
        the model is loaded from ``config.inference.checkpoint_path``.
    graph:
        Pre-built :class:`~src.data.graph_builder.LaneGraph`. If ``None``,
        built from the topology config.
    loader:
        :class:`~src.data.db_loader.TrafficMetricsLoader`. If ``None``,
        constructed from config.
    """

    def __init__(
        self,
        config: Config | None = None,
        model: STGCN | None = None,
        graph: LaneGraph | None = None,
        loader: TrafficMetricsLoader | None = None,
    ) -> None:
        self._cfg = config or get_config()
        self._icfg = self._cfg.inference
        self._device = torch.device(self._icfg.device)

        self._graph = graph or build_graph(self._cfg)
        self._loader = loader or TrafficMetricsLoader(self._cfg)
        self._estimator = CongestionEstimator(self._cfg)

        if model is not None:
            self._model = model.to(self._device)
        else:
            self._model = STGCN.load(
                self._icfg.checkpoint_path,
                num_nodes=self._graph.num_nodes,
                config=self._cfg,
            ).to(self._device)

        self._model.eval()
        logger.info(
            "TrafficPredictor ready — nodes=%d  device=%s  horizon=%d steps",
            self._graph.num_nodes,
            self._device,
            self._cfg.features.output_horizon,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict(self, lookback_minutes: int | None = None) -> list[LaneForecast]:
        """
        Fetch recent data, run inference, and return per-lane forecasts.

        Parameters
        ----------
        lookback_minutes:
            If set, overrides :attr:`Config.inference.db_lookback_minutes` for the
            DB fetch window (useful when the hosting API fixes history length).

        Returns
        -------
        list[LaneForecast]
            One :class:`LaneForecast` per graph node (lane).
        """
        now = datetime.now(tz=timezone.utc)

        minutes = (
            lookback_minutes
            if lookback_minutes is not None
            else self._icfg.db_lookback_minutes
        )
        df = self._loader.load_recent(minutes)
        if df.empty:
            logger.warning("No recent data available — returning empty forecasts.")
            return []

        x = self._build_input(df)
        if x is None:
            logger.warning("Insufficient history for T_in=%d steps.", self._cfg.features.input_window)
            return []

        edge_index = self._graph.edge_index.to(self._device)
        with torch.no_grad():
            output = self._model(x, edge_index)

        forecasts = self._decode(output, forecast_origin=now)
        return forecasts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_input(self, df) -> Tensor | None:
        """
        Convert a DataFrame into a model input tensor ``[1, T_in, N, F]``.

        Uses the most recent ``T_in`` windows available per node.
        """
        feat_cols = self._cfg.features.node_features
        T_in = self._cfg.features.input_window
        N = self._graph.num_nodes
        F = self._cfg.features.num_node_features

        all_times = sorted(df["window_start"].unique())
        if len(all_times) < T_in:
            return None

        recent_times = all_times[-T_in:]
        df_recent = df[df["window_start"].isin(recent_times)].copy()

        signal = np.zeros((T_in, N, F), dtype=np.float32)
        time_idx = {t: i for i, t in enumerate(recent_times)}

        for _, row in df_recent.iterrows():
            key = (row["camera_id"], int(row["lane_id"]))
            if key not in self._graph.node_index:
                continue
            n_idx = self._graph.node_index[key]
            t_idx = time_idx.get(row["window_start"])
            if t_idx is None:
                continue
            for f_idx, col in enumerate(feat_cols):
                val = row.get(col, 0.0)
                signal[t_idx, n_idx, f_idx] = float(val) if val is not None else 0.0

        x = torch.from_numpy(signal).unsqueeze(0).to(self._device)  # [1, T_in, N, F]
        return x

    def _decode(
        self, output, forecast_origin: datetime
    ) -> list[LaneForecast]:
        """Convert raw model output tensors into :class:`LaneForecast` objects."""
        T_out = self._cfg.features.output_horizon
        step_sec = self._cfg.features.window_size_seconds
        norm_count_max = self._cfg.features.normalization.vehicle_count_max

        from datetime import timedelta

        forecast_timestamps = [
            forecast_origin + timedelta(seconds=(i + 1) * step_sec)
            for i in range(T_out)
        ]

        # Convert tensors to numpy [T_out, N]
        count_np: np.ndarray | None = None
        prob_np: np.ndarray | None = None
        level_np: np.ndarray | None = None

        if output.vehicle_count is not None:
            count_np = output.vehicle_count[0].cpu().numpy()   # [T_out, N]
        if output.congestion_prob is not None:
            prob_np = output.congestion_prob[0].cpu().numpy()  # [T_out, N]
        if output.congestion_level is not None:
            level_np = output.congestion_level[0].cpu().numpy()  # [T_out, N, 3]

        # Congestion windows (start / end times)
        cw_map: dict[tuple[str, int], CongestionWindow] = {}
        if prob_np is not None:
            windows = self._estimator.estimate_batch(
                prob_np, self._graph.index_to_node, forecast_origin
            )
            cw_map = {(w.camera_id, w.lane_id): w for w in windows}

        forecasts: list[LaneForecast] = []
        for n_idx, (cam, lane) in enumerate(self._graph.index_to_node):
            cw = cw_map.get((cam, lane))

            counts = (
                (count_np[:, n_idx] * norm_count_max).tolist()
                if count_np is not None else [0.0] * T_out
            )
            probs = (
                prob_np[:, n_idx].tolist()
                if prob_np is not None else [0.0] * T_out
            )
            levels: list[CongestionLabel] = [
                _IDX_TO_LABEL.get(int(np.argmax(level_np[t, n_idx])), "LOW")
                for t in range(T_out)
            ] if level_np is not None else ["LOW"] * T_out

            forecasts.append(
                LaneForecast(
                    camera_id=cam,
                    lane_id=lane,
                    predicted_vehicle_counts=counts,
                    congestion_probabilities=probs,
                    congestion_levels=levels,
                    congestion_start=cw.congestion_start if cw else None,
                    congestion_end=cw.congestion_end if cw else None,
                    peak_congestion_probability=cw.peak_probability if cw else 0.0,
                    forecast_timestamps=forecast_timestamps,
                )
            )

        return forecasts
