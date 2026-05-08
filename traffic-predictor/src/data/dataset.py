"""
PyTorch Dataset for spatio-temporal traffic forecasting.

Each sample is a pair ``(x, y)`` where:

* ``x``: Tensor of shape ``[T_in, N, F]``  — past T_in windows across N lane-nodes,
  each node with F features.
* ``y``: dict of target tensors, all shape ``[T_out, N]`` (or ``[T_out, N, C]``
  for multi-class classification):

  * ``vehicle_count``   float32  — normalised vehicle count
  * ``congestion_prob`` float32  — congestion probability (0 / 1 binarised from level)
  * ``congestion_level`` int64   — class index 0=LOW 1=MEDIUM 2=HIGH

Sliding windows are extracted from the full time-series with a step of 1.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import Tensor
from torch.utils.data import Dataset

from src.config import Config, get_config
from src.data.graph_builder import LaneGraph, build_graph

logger = logging.getLogger(__name__)

# Congestion level → probability label
_LEVEL_TO_PROB: dict[int, float] = {0: 0.0, 1: 0.5, 2: 1.0}


class TrafficGraphDataset(Dataset[dict[str, Any]]):
    """
    Sliding-window spatio-temporal dataset.

    Parameters
    ----------
    df:
        Preprocessed DataFrame from :class:`~src.data.db_loader.TrafficMetricsLoader`.
        Must contain columns: ``camera_id``, ``lane_id``, ``window_start`` plus
        all feature columns listed in ``config.features.node_features``.
    graph:
        Pre-built :class:`~src.data.graph_builder.LaneGraph`.
    config:
        App config. Defaults to global singleton.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        graph: LaneGraph,
        config: Config | None = None,
    ) -> None:
        self._cfg = config or get_config()
        self._graph = graph
        feat_cfg = self._cfg.features

        T_in = feat_cfg.input_window
        T_out = feat_cfg.output_horizon
        N = graph.num_nodes
        F = feat_cfg.num_node_features

        # Build a dense [T_total, N, F] array aligned to graph node order
        signal, count_raw, prob_raw, level_raw = self._build_signal(df)

        T_total = signal.shape[0]
        min_len = T_in + T_out
        if T_total < min_len:
            raise ValueError(
                f"Time series has {T_total} steps, need at least {min_len} "
                f"(T_in={T_in} + T_out={T_out}). Load more history."
            )

        # Pre-compute all valid window start indices
        starts = list(range(T_total - min_len + 1))
        self._starts = starts
        self._signal = signal          # [T_total, N, F]
        self._count_raw = count_raw    # [T_total, N]
        self._prob_raw = prob_raw      # [T_total, N]
        self._level_raw = level_raw    # [T_total, N]
        self._T_in = T_in
        self._T_out = T_out

        logger.info(
            "Dataset: %d samples  [T=%d, N=%d, F=%d]  (T_in=%d, T_out=%d)",
            len(starts), T_total, N, F, T_in, T_out,
        )

    # ------------------------------------------------------------------
    # Dataset protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._starts)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        s = self._starts[idx]
        e_in = s + self._T_in
        e_out = e_in + self._T_out

        x = torch.from_numpy(self._signal[s:e_in]).float()           # [T_in,  N, F]
        y_count = torch.from_numpy(self._count_raw[e_in:e_out]).float()   # [T_out, N]
        y_prob  = torch.from_numpy(self._prob_raw[e_in:e_out]).float()    # [T_out, N]
        y_level = torch.from_numpy(self._level_raw[e_in:e_out]).long()    # [T_out, N]

        return {
            "x": x,
            "edge_index": self._graph.edge_index,
            "y_vehicle_count": y_count,
            "y_congestion_prob": y_prob,
            "y_congestion_level": y_level,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_signal(
        self, df: pd.DataFrame
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Pivot the flat DataFrame into 3-D arrays ``[T, N, F]`` and ``[T, N]``
        aligned to the graph node ordering.

        Missing nodes are zero-padded.
        """
        feat_cols = self._cfg.features.node_features
        N = self._graph.num_nodes

        # Determine the full time grid from the data
        all_times = sorted(df["window_start"].unique())
        T = len(all_times)
        time_idx = {t: i for i, t in enumerate(all_times)}

        signal   = np.zeros((T, N, len(feat_cols)), dtype=np.float32)
        count_r  = np.zeros((T, N), dtype=np.float32)
        prob_r   = np.zeros((T, N), dtype=np.float32)
        level_r  = np.zeros((T, N), dtype=np.int64)

        for _, row in df.iterrows():
            key = (row["camera_id"], int(row["lane_id"]))
            if key not in self._graph.node_index:
                continue
            n_idx = self._graph.node_index[key]
            t_idx = time_idx[row["window_start"]]

            for f_idx, col in enumerate(feat_cols):
                val = row.get(col, 0.0)
                signal[t_idx, n_idx, f_idx] = float(val) if pd.notna(val) else 0.0

            count_r[t_idx, n_idx] = float(row.get("vehicle_count", 0.0) or 0.0)
            lvl = int(row.get("congestion_level_idx", 0) or 0)
            level_r[t_idx, n_idx] = lvl
            prob_r[t_idx, n_idx] = _LEVEL_TO_PROB.get(lvl, 0.0)

        return signal, count_r, prob_r, level_r
