"""
ST-GCN traffic forecasting model.

Architecture overview
---------------------

    Input:  x  [B, T_in, N, F]
              ↓
    Linear input projection → [B, T_in, N, H]
              ↓
    STGCNBlock × num_blocks  → [B, T_in, N, H]
              ↓
    Flatten T × H → temporal context  [B, N, T_in * H]
              ↓  (via linear readout)
    ┌────────────────────────────────────┐
    │  Head: vehicle count (regression)  │  → [B, T_out, N]   float32
    │  Head: congestion prob (sigmoid)   │  → [B, T_out, N]   float32 ∈ [0,1]
    │  Head: congestion level (3-class)  │  → [B, T_out, N, 3]  raw logits
    └────────────────────────────────────┘

Each output head is a lightweight 2-layer MLP that maps the shared context
to the full forecast horizon ``T_out``.

The model is designed for **multi-task learning**: a single forward pass
produces all three outputs and the training loop combines their losses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
from torch import Tensor

from src.config import Config, get_config
from src.model.layers import STGCNBlock

logger = logging.getLogger(__name__)

_NUM_CONGESTION_CLASSES = 3  # LOW, MEDIUM, HIGH


@dataclass
class STGCNOutput:
    """Container for all model output heads."""

    vehicle_count: Tensor | None = None      # [B, T_out, N]
    congestion_prob: Tensor | None = None    # [B, T_out, N]
    congestion_level: Tensor | None = None   # [B, T_out, N, 3]  logits


class _ForecastHead(nn.Module):
    """
    Shared MLP readout that maps a per-node context vector to T_out scalars.

    Parameters
    ----------
    context_dim:
        Dimensionality of the per-node context (T_in × H after flattening).
    T_out:
        Number of future time steps to predict.
    out_features:
        Number of output values per time step (1 for regression / prob,
        3 for class logits).
    """

    def __init__(self, context_dim: int, T_out: int, out_features: int = 1) -> None:
        super().__init__()
        hidden = max(context_dim // 2, 64)
        self.net = nn.Sequential(
            nn.Linear(context_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, T_out * out_features),
        )
        self._T_out = T_out
        self._out = out_features

    def forward(self, ctx: Tensor) -> Tensor:
        """
        Parameters
        ----------
        ctx:
            ``[B, N, context_dim]``

        Returns
        -------
        Tensor
            ``[B, T_out, N]`` when out_features=1, else ``[B, T_out, N, out_features]``.
        """
        B, N, _ = ctx.shape
        out = self.net(ctx)                          # [B, N, T_out * out_features]
        out = out.reshape(B, N, self._T_out, self._out)
        out = out.permute(0, 2, 1, 3)               # [B, T_out, N, out_features]
        if self._out == 1:
            return out.squeeze(-1)                   # [B, T_out, N]
        return out                                   # [B, T_out, N, out_features]


class STGCN(nn.Module):
    """
    Spatio-Temporal Graph Convolutional Network for lane-level traffic forecasting.

    Parameters
    ----------
    num_node_features:
        F — number of input features per node (from config).
    num_nodes:
        N — total number of lane-nodes in the graph.
    config:
        Application config. Defaults to global singleton.
    """

    def __init__(
        self,
        num_node_features: int,
        num_nodes: int,
        config: Config | None = None,
    ) -> None:
        super().__init__()
        self._cfg = config or get_config()
        mcfg = self._cfg.model
        fcfg = self._cfg.features

        H = mcfg.hidden_channels
        T_in = fcfg.input_window
        T_out = fcfg.output_horizon
        context_dim = T_in * H

        # Input projection: F → H
        self.input_proj = nn.Linear(num_node_features, H)

        # Stacked ST-GCN blocks
        self.blocks = nn.ModuleList(
            [
                STGCNBlock(
                    in_channels=H,
                    out_channels=H,
                    K=mcfg.cheb_k,
                    temporal_kernel_size=mcfg.temporal_kernel_size,
                    dropout=mcfg.dropout,
                )
                for _ in range(mcfg.num_stgcn_blocks)
            ]
        )

        # Output heads
        heads_cfg = mcfg.heads
        self.head_count: nn.Module | None = None
        self.head_prob: nn.Module | None = None
        self.head_level: nn.Module | None = None

        if heads_cfg.vehicle_count:
            self.head_count = _ForecastHead(context_dim, T_out, out_features=1)
        if heads_cfg.congestion_prob:
            self.head_prob = _ForecastHead(context_dim, T_out, out_features=1)
        if heads_cfg.congestion_level:
            self.head_level = _ForecastHead(
                context_dim, T_out, out_features=_NUM_CONGESTION_CLASSES
            )

        self._T_in = T_in
        self._H = H

        n_params = sum(p.numel() for p in self.parameters() if p.requires_grad)
        logger.info(
            "STGCN initialised — nodes=%d  features=%d  hidden=%d  blocks=%d  "
            "T_in=%d  T_out=%d  params=%s",
            num_nodes, num_node_features, H, mcfg.num_stgcn_blocks,
            T_in, T_out, f"{n_params:,}",
        )

    def forward(self, x: Tensor, edge_index: Tensor) -> STGCNOutput:
        """
        Parameters
        ----------
        x:
            Node feature tensor ``[B, T_in, N, F]``.
        edge_index:
            Graph connectivity ``[2, E]`` (shared across all samples in batch).

        Returns
        -------
        STGCNOutput
            Named tensors for each active output head.
        """
        # Input projection: F → H
        h = self.input_proj(x)                       # [B, T_in, N, H]

        # Spatio-temporal encoding
        for block in self.blocks:
            h = block(h, edge_index)                 # [B, T_in, N, H]

        # Flatten time × hidden into a context vector per node
        B, T, N, H = h.shape
        ctx = h.permute(0, 2, 1, 3).reshape(B, N, T * H)  # [B, N, T*H]

        out = STGCNOutput()

        if self.head_count is not None:
            raw = self.head_count(ctx)               # [B, T_out, N]
            out.vehicle_count = torch.relu(raw)      # counts are non-negative

        if self.head_prob is not None:
            out.congestion_prob = torch.sigmoid(self.head_prob(ctx))  # [B, T_out, N]

        if self.head_level is not None:
            out.congestion_level = self.head_level(ctx)  # [B, T_out, N, 3]  logits

        return out

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Persist model weights and config metadata to *path*."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.state_dict(),
                "model_config": {
                    "num_node_features": self.input_proj.in_features,
                },
            },
            path,
        )
        logger.info("Model saved → %s", path)

    @classmethod
    def load(
        cls,
        path: str | Path,
        num_nodes: int,
        config: Config | None = None,
    ) -> "STGCN":
        """Load a previously saved model from *path*."""
        path = Path(path)
        checkpoint: dict[str, Any] = torch.load(path, map_location="cpu", weights_only=False)
        mcfg = checkpoint["model_config"]
        model = cls(
            num_node_features=mcfg["num_node_features"],
            num_nodes=num_nodes,
            config=config,
        )
        model.load_state_dict(checkpoint["state_dict"])
        logger.info("Model loaded ← %s", path)
        return model


def compute_loss(
    output: STGCNOutput,
    targets: dict[str, Tensor],
    config: Config | None = None,
) -> tuple[Tensor, dict[str, Tensor]]:
    """
    Compute the combined multi-task loss.

    Returns
    -------
    total_loss:
        Weighted sum of individual task losses.
    loss_dict:
        Per-task loss values for logging.
    """
    cfg = config or get_config()
    weights = cfg.model.loss_weights
    loss_dict: dict[str, Tensor] = {}

    total = torch.zeros(1, device=next(iter(targets.values())).device)

    if output.vehicle_count is not None and "y_vehicle_count" in targets:
        l = nn.functional.mse_loss(output.vehicle_count, targets["y_vehicle_count"])
        loss_dict["count"] = l
        total = total + weights.vehicle_count * l

    if output.congestion_prob is not None and "y_congestion_prob" in targets:
        l = nn.functional.binary_cross_entropy(
            output.congestion_prob, targets["y_congestion_prob"]
        )
        loss_dict["prob"] = l
        total = total + weights.congestion_prob * l

    if output.congestion_level is not None and "y_congestion_level" in targets:
        B, T_out, N, C = output.congestion_level.shape
        logits = output.congestion_level.reshape(B * T_out * N, C)
        labels = targets["y_congestion_level"].reshape(B * T_out * N)
        l = nn.functional.cross_entropy(logits, labels)
        loss_dict["level"] = l
        total = total + weights.congestion_level * l

    return total.squeeze(), loss_dict
