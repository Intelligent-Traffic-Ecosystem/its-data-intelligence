"""
Unit tests for graph_builder.py
"""

from __future__ import annotations

import pytest
import torch

from src.config import (
    CameraTopology,
    Config,
    CrossCameraEdge,
    TopologyConfig,
)
from src.data.graph_builder import LaneGraph, build_graph


def _make_config(cameras, cross_edges=None) -> Config:
    cfg = Config()
    cfg.topology = TopologyConfig(
        cameras=[CameraTopology(**c) for c in cameras],
        cross_camera_edges=[CrossCameraEdge(**e) for e in (cross_edges or [])],
    )
    return cfg


class TestBuildGraph:
    def test_single_camera_node_count(self):
        cfg = _make_config([{"camera_id": "cam-001", "lane_ids": [1, 2, 3]}])
        g = build_graph(cfg)
        assert g.num_nodes == 3

    def test_node_index_keys(self):
        cfg = _make_config([{"camera_id": "cam-001", "lane_ids": [1, 2]}])
        g = build_graph(cfg)
        assert ("cam-001", 1) in g.node_index
        assert ("cam-001", 2) in g.node_index

    def test_edge_index_shape(self):
        cfg = _make_config([{"camera_id": "cam-001", "lane_ids": [1, 2, 3]}])
        g = build_graph(cfg)
        assert g.edge_index.shape[0] == 2
        assert g.edge_index.shape[1] > 0

    def test_self_loops_present(self):
        cfg = _make_config([{"camera_id": "cam-001", "lane_ids": [1, 2]}])
        g = build_graph(cfg)
        # Every node should have a self-loop
        edge_set = set(map(tuple, g.edge_index.t().tolist()))
        for i in range(g.num_nodes):
            assert (i, i) in edge_set

    def test_adjacent_lanes_connected(self):
        cfg = _make_config([{"camera_id": "cam-001", "lane_ids": [1, 2]}])
        g = build_graph(cfg)
        i = g.node_index[("cam-001", 1)]
        j = g.node_index[("cam-001", 2)]
        edge_set = set(map(tuple, g.edge_index.t().tolist()))
        assert (i, j) in edge_set
        assert (j, i) in edge_set  # bidirectional

    def test_cross_camera_edges(self):
        cfg = _make_config(
            cameras=[
                {"camera_id": "cam-001", "lane_ids": [1, 2]},
                {"camera_id": "cam-002", "lane_ids": [1, 2]},
            ],
            cross_edges=[
                {
                    "from_camera": "cam-001",
                    "from_lane": 2,
                    "to_camera": "cam-002",
                    "to_lane": 1,
                }
            ],
        )
        g = build_graph(cfg)
        src = g.node_index[("cam-001", 2)]
        dst = g.node_index[("cam-002", 1)]
        edge_set = set(map(tuple, g.edge_index.t().tolist()))
        assert (src, dst) in edge_set
        assert (dst, src) in edge_set

    def test_multi_camera_node_count(self):
        cfg = _make_config(
            [
                {"camera_id": "cam-001", "lane_ids": [1, 2, 3]},
                {"camera_id": "cam-002", "lane_ids": [1, 2]},
            ]
        )
        g = build_graph(cfg)
        assert g.num_nodes == 5

    def test_index_to_node_roundtrip(self):
        cfg = _make_config([{"camera_id": "cam-001", "lane_ids": [1, 2, 3]}])
        g = build_graph(cfg)
        for key, idx in g.node_index.items():
            assert g.index_to_node[idx] == key

    def test_edge_index_dtype(self):
        cfg = _make_config([{"camera_id": "cam-001", "lane_ids": [1, 2]}])
        g = build_graph(cfg)
        assert g.edge_index.dtype == torch.long
