"""
Unit tests for the ST-GCN model layers and forward pass.

These tests run entirely on CPU with small synthetic graphs so they require
no GPU and complete in a few seconds.
"""

from __future__ import annotations

import torch
import pytest

from src.config import Config, CameraTopology, TopologyConfig
from src.data.graph_builder import build_graph
from src.model.layers import GatedTCN, ChebConvSpatial, STGCNBlock, _batch_edge_index
from src.model.stgcn import STGCN, STGCNOutput, compute_loss


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _small_config() -> Config:
    cfg = Config()
    cfg.topology = TopologyConfig(
        cameras=[CameraTopology(camera_id="cam-001", lane_ids=[1, 2, 3])]
    )
    cfg.features.node_features = [
        "vehicle_count", "avg_speed_kmh", "stopped_ratio",
        "congestion_score", "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ]
    cfg.features.input_window = 6
    cfg.features.output_horizon = 12
    cfg.model.hidden_channels = 16
    cfg.model.num_stgcn_blocks = 2
    cfg.model.temporal_kernel_size = 3
    cfg.model.cheb_k = 2
    cfg.model.dropout = 0.0
    return cfg


def _make_model(cfg: Config, num_nodes: int = 3) -> STGCN:
    return STGCN(
        num_node_features=cfg.features.num_node_features,
        num_nodes=num_nodes,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Layer tests
# ---------------------------------------------------------------------------


class TestGatedTCN:
    def test_output_shape(self):
        B, T, N, C = 2, 6, 3, 16
        layer = GatedTCN(channels=C, kernel_size=3)
        x = torch.randn(B, T, N, C)
        out = layer(x)
        assert out.shape == (B, T, N, C)

    def test_residual_preserved(self):
        B, T, N, C = 1, 4, 2, 8
        layer = GatedTCN(channels=C, kernel_size=1)
        x = torch.zeros(B, T, N, C)
        out = layer(x)
        # With zero input + kernel_size=1, output includes residual (0 + 0 = 0)
        assert out.shape == x.shape


class TestBatchEdgeIndex:
    def test_shape(self):
        edge_index = torch.tensor([[0, 1, 1, 0], [1, 0, 1, 0]])  # N=2, 4 edges
        batched = _batch_edge_index(edge_index, batch_size=3, num_nodes=2)
        assert batched.shape == (2, 12)  # 3 * 4 edges

    def test_offset_correct(self):
        edge_index = torch.tensor([[0, 1], [1, 0]])
        batched = _batch_edge_index(edge_index, batch_size=2, num_nodes=3)
        # Second batch item edges should be offset by 3
        # Result cols 2,3 should have indices 3,4
        assert batched[0, 2].item() == 3
        assert batched[1, 2].item() == 4


class TestChebConvSpatial:
    def test_output_shape(self):
        B, T, N, C_in, C_out = 2, 4, 3, 8, 16
        cfg = _small_config()
        graph = build_graph(cfg)
        layer = ChebConvSpatial(C_in, C_out, K=2)
        x = torch.randn(B, T, N, C_in)
        out = layer(x, graph.edge_index)
        assert out.shape == (B, T, N, C_out)


class TestSTGCNBlock:
    def test_output_shape_same_channels(self):
        cfg = _small_config()
        graph = build_graph(cfg)
        block = STGCNBlock(in_channels=16, out_channels=16, K=2, dropout=0.0)
        x = torch.randn(2, 6, 3, 16)
        out = block(x, graph.edge_index)
        assert out.shape == (2, 6, 3, 16)

    def test_output_shape_different_channels(self):
        cfg = _small_config()
        graph = build_graph(cfg)
        block = STGCNBlock(in_channels=8, out_channels=16, K=2, dropout=0.0)
        x = torch.randn(2, 6, 3, 8)
        out = block(x, graph.edge_index)
        assert out.shape == (2, 6, 3, 16)


# ---------------------------------------------------------------------------
# STGCN model tests
# ---------------------------------------------------------------------------


class TestSTGCN:
    def test_forward_output_shapes(self):
        cfg = _small_config()
        graph = build_graph(cfg)
        model = _make_model(cfg, num_nodes=graph.num_nodes)
        model.eval()

        B = 2
        x = torch.randn(B, cfg.features.input_window, graph.num_nodes, cfg.features.num_node_features)
        output: STGCNOutput = model(x, graph.edge_index)

        T_out = cfg.features.output_horizon
        N = graph.num_nodes

        assert output.vehicle_count is not None
        assert output.vehicle_count.shape == (B, T_out, N)

        assert output.congestion_prob is not None
        assert output.congestion_prob.shape == (B, T_out, N)
        # Sigmoid output must be in [0, 1]
        assert output.congestion_prob.min().item() >= 0.0
        assert output.congestion_prob.max().item() <= 1.0

        assert output.congestion_level is not None
        assert output.congestion_level.shape == (B, T_out, N, 3)

    def test_vehicle_count_non_negative(self):
        cfg = _small_config()
        graph = build_graph(cfg)
        model = _make_model(cfg, num_nodes=graph.num_nodes)
        model.eval()
        x = torch.randn(1, cfg.features.input_window, graph.num_nodes, cfg.features.num_node_features)
        output = model(x, graph.edge_index)
        assert output.vehicle_count.min().item() >= 0.0

    def test_loss_computation(self):
        cfg = _small_config()
        graph = build_graph(cfg)
        model = _make_model(cfg, num_nodes=graph.num_nodes)

        B, T_out, N = 2, cfg.features.output_horizon, graph.num_nodes
        x = torch.randn(B, cfg.features.input_window, N, cfg.features.num_node_features)
        output = model(x, graph.edge_index)

        targets = {
            "y_vehicle_count":   torch.rand(B, T_out, N),
            "y_congestion_prob": torch.rand(B, T_out, N),
            "y_congestion_level": torch.randint(0, 3, (B, T_out, N)),
        }
        loss, breakdown = compute_loss(output, targets, cfg)
        assert loss.item() > 0
        assert "count" in breakdown
        assert "prob" in breakdown
        assert "level" in breakdown

    def test_gradient_flows(self):
        cfg = _small_config()
        graph = build_graph(cfg)
        model = _make_model(cfg, num_nodes=graph.num_nodes)

        B, T_out, N = 1, cfg.features.output_horizon, graph.num_nodes
        x = torch.randn(B, cfg.features.input_window, N, cfg.features.num_node_features)
        output = model(x, graph.edge_index)
        targets = {
            "y_vehicle_count": torch.rand(B, T_out, N),
            "y_congestion_prob": torch.rand(B, T_out, N),
            "y_congestion_level": torch.randint(0, 3, (B, T_out, N)),
        }
        loss, _ = compute_loss(output, targets, cfg)
        loss.backward()

        for name, param in model.named_parameters():
            if param.requires_grad and param.grad is not None:
                assert not torch.isnan(param.grad).any(), f"NaN gradient in {name}"
