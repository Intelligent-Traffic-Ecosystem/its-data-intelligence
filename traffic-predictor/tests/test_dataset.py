"""
Unit tests for dataset.py — covers the sliding-window extraction logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import torch

from src.config import Config, CameraTopology, TopologyConfig
from src.data.dataset import TrafficGraphDataset
from src.data.graph_builder import build_graph


def _make_config(T_in: int = 6, T_out: int = 12) -> Config:
    cfg = Config()
    cfg.topology = TopologyConfig(
        cameras=[CameraTopology(camera_id="cam-001", lane_ids=[1, 2])]
    )
    cfg.features.node_features = [
        "vehicle_count", "avg_speed_kmh", "stopped_ratio",
        "congestion_score", "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ]
    cfg.features.input_window = T_in
    cfg.features.output_horizon = T_out
    cfg.features.window_size_seconds = 5
    return cfg


def _make_df(
    camera_id: str = "cam-001",
    lane_ids: list[int] = [1, 2],
    n_steps: int = 30,
    start: datetime | None = None,
) -> pd.DataFrame:
    start = start or datetime(2026, 5, 1, tzinfo=timezone.utc)
    rows = []
    for step in range(n_steps):
        ts = start + timedelta(seconds=step * 5)
        for lane in lane_ids:
            rows.append({
                "camera_id": camera_id,
                "lane_id": lane,
                "window_start": ts,
                "vehicle_count": float(step % 10) / 10.0,
                "avg_speed_kmh": 0.5,
                "stopped_ratio": 0.1,
                "congestion_score": 0.2,
                "hour_sin": 0.0,
                "hour_cos": 1.0,
                "dow_sin": 0.0,
                "dow_cos": 1.0,
                "congestion_level_idx": 0,
            })
    return pd.DataFrame(rows)


class TestTrafficGraphDataset:
    def test_length(self):
        cfg = _make_config(T_in=6, T_out=12)
        graph = build_graph(cfg)
        df = _make_df(n_steps=30)
        ds = TrafficGraphDataset(df, graph, cfg)
        # Total time steps = 30, min_len = 18, valid windows = 30 - 18 + 1 = 13
        assert len(ds) == 30 - 6 - 12 + 1

    def test_item_x_shape(self):
        cfg = _make_config(T_in=6, T_out=12)
        graph = build_graph(cfg)
        df = _make_df(n_steps=30)
        ds = TrafficGraphDataset(df, graph, cfg)
        item = ds[0]
        assert item["x"].shape == (6, graph.num_nodes, cfg.features.num_node_features)

    def test_item_y_count_shape(self):
        cfg = _make_config(T_in=6, T_out=12)
        graph = build_graph(cfg)
        df = _make_df(n_steps=30)
        ds = TrafficGraphDataset(df, graph, cfg)
        item = ds[0]
        assert item["y_vehicle_count"].shape == (12, graph.num_nodes)

    def test_item_y_level_dtype(self):
        cfg = _make_config(T_in=6, T_out=12)
        graph = build_graph(cfg)
        df = _make_df(n_steps=30)
        ds = TrafficGraphDataset(df, graph, cfg)
        item = ds[0]
        assert item["y_congestion_level"].dtype == torch.int64

    def test_item_y_prob_range(self):
        cfg = _make_config(T_in=6, T_out=12)
        graph = build_graph(cfg)
        df = _make_df(n_steps=30)
        ds = TrafficGraphDataset(df, graph, cfg)
        for i in range(len(ds)):
            prob = ds[i]["y_congestion_prob"]
            assert prob.min().item() >= 0.0
            assert prob.max().item() <= 1.0

    def test_edge_index_in_item(self):
        cfg = _make_config()
        graph = build_graph(cfg)
        df = _make_df(n_steps=25)
        ds = TrafficGraphDataset(df, graph, cfg)
        item = ds[0]
        assert "edge_index" in item
        assert item["edge_index"].shape[0] == 2

    def test_raises_if_too_few_steps(self):
        cfg = _make_config(T_in=6, T_out=12)
        graph = build_graph(cfg)
        df = _make_df(n_steps=10)  # only 10, need 18
        with pytest.raises(ValueError, match="at least"):
            TrafficGraphDataset(df, graph, cfg)

    def test_unknown_nodes_ignored(self):
        cfg = _make_config()
        graph = build_graph(cfg)
        df = _make_df(n_steps=25)
        # Add rows for a lane not in the graph
        extra = _make_df(camera_id="cam-UNKNOWN", lane_ids=[99], n_steps=25)
        combined = pd.concat([df, extra], ignore_index=True)
        # Should not raise; unknown node rows are simply skipped
        ds = TrafficGraphDataset(combined, graph, cfg)
        assert len(ds) > 0
