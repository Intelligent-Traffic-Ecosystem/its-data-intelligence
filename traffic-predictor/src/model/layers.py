"""
Primitive building blocks for the ST-GCN model.

Spatial layer
-------------
:class:`ChebConvSpatial` — Chebyshev spectral graph convolution (``ChebConv``
from PyG) applied independently at each time step.

Temporal layer
--------------
:class:`GatedTCN` — Gated Temporal Convolutional Network block with a
residual projection.  Uses two parallel 1-D convolutions (tanh + sigmoid)
whose element-wise product forms the gated output (analogous to GLU / LSTM
gates), following the WaveNet / STGCN literature.

ST-GCN block
------------
:class:`STGCNBlock` — a single Spatio-Temporal block::

    input → Temporal → Spatial → Temporal → LayerNorm + Dropout → output
                 ↑________________________________↓ (residual if shapes match)

All operations keep the shape ``[B, T, N, C]`` (batch, time, nodes, channels).
"""

from __future__ import annotations

import torch
import torch.nn as nn
from torch import Tensor
from torch_geometric.nn import ChebConv


class ChebConvSpatial(nn.Module):
    """
    Chebyshev spectral graph convolution applied at every time step.

    Reshapes ``[B, T, N, C_in]`` → applies ChebConv per step → ``[B, T, N, C_out]``.

    Parameters
    ----------
    in_channels:
        Number of input node features.
    out_channels:
        Number of output node features.
    K:
        Chebyshev polynomial order (neighbourhood depth).
    """

    def __init__(self, in_channels: int, out_channels: int, K: int = 3) -> None:
        super().__init__()
        self.conv = ChebConv(in_channels, out_channels, K=K)

    def forward(self, x: Tensor, edge_index: Tensor) -> Tensor:
        """
        Parameters
        ----------
        x:
            Node features ``[B, T, N, C_in]``.
        edge_index:
            Graph connectivity ``[2, E]``.

        Returns
        -------
        Tensor
            ``[B, T, N, C_out]``
        """
        B, T, N, C = x.shape
        # Flatten batch × time into a single batch dimension
        x_flat = x.reshape(B * T, N, C)

        # ChebConv expects [total_nodes_in_batch, C]; use a batch-of-graphs approach
        # via manual looping — keeps edge_index simple (single graph, reused per step)
        pieces: list[Tensor] = []
        for t in range(T):
            xt = x[:, t, :, :].reshape(B * 1, N, C)
            # Repeat the same graph for each item in the batch via batching trick:
            # shift edge_index node indices by N for each batch item
            batch_edge = _batch_edge_index(edge_index, B, N)
            xt_flat = xt.reshape(B * N, C)
            out_flat = self.conv(xt_flat, batch_edge)    # [B*N, C_out]
            pieces.append(out_flat.reshape(B, N, -1))

        return torch.stack(pieces, dim=1)                # [B, T, N, C_out]


class GatedTCN(nn.Module):
    """
    Gated Temporal Convolutional Network block.

    Operates on the time dimension of ``[B, T, N, C]`` tensors via 1-D
    depthwise-separable convolutions.  The gating (tanh × sigmoid) captures
    non-linear temporal dynamics while keeping parameter counts low.

    Parameters
    ----------
    channels:
        Number of input/output channels (same, so residual is trivial).
    kernel_size:
        Temporal kernel size (applied along the T axis).
    dilation:
        Dilation factor for the temporal convolution.
    """

    def __init__(
        self, channels: int, kernel_size: int = 3, dilation: int = 1
    ) -> None:
        super().__init__()
        padding = (kernel_size - 1) * dilation // 2
        # Two parallel convolutions for tanh and sigmoid gates
        self.tanh_conv = nn.Conv1d(
            channels, channels, kernel_size,
            padding=padding, dilation=dilation, groups=channels,
        )
        self.sig_conv = nn.Conv1d(
            channels, channels, kernel_size,
            padding=padding, dilation=dilation, groups=channels,
        )
        self.pointwise = nn.Conv1d(channels, channels, kernel_size=1)

    def forward(self, x: Tensor) -> Tensor:
        """
        Parameters
        ----------
        x:
            ``[B, T, N, C]``

        Returns
        -------
        Tensor
            ``[B, T, N, C]``
        """
        B, T, N, C = x.shape
        # Merge N into the batch axis; treat each node independently
        x_n = x.permute(0, 2, 3, 1).reshape(B * N, C, T)   # [B*N, C, T]
        gate = torch.tanh(self.tanh_conv(x_n)) * torch.sigmoid(self.sig_conv(x_n))
        out = self.pointwise(gate)                            # [B*N, C, T]
        out = out.reshape(B, N, C, T).permute(0, 3, 1, 2)   # [B, T, N, C]
        return out + x                                        # residual


class STGCNBlock(nn.Module):
    """
    One Spatio-Temporal Graph Convolutional block.

    Structure::

        x  →  GatedTCN  →  ChebConvSpatial  →  GatedTCN  →  LayerNorm  →  y
        |_____________________________________________________↑  (residual proj)

    Parameters
    ----------
    in_channels:
        Input channel dimension.
    out_channels:
        Output channel dimension.
    K:
        Chebyshev order for the spatial layer.
    temporal_kernel_size:
        Kernel size for both GatedTCN layers.
    dropout:
        Dropout probability applied after the block.
    """

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        K: int = 3,
        temporal_kernel_size: int = 3,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.tcn1 = GatedTCN(in_channels, kernel_size=temporal_kernel_size)
        self.spatial = ChebConvSpatial(in_channels, out_channels, K=K)
        self.tcn2 = GatedTCN(out_channels, kernel_size=temporal_kernel_size)
        self.norm = nn.LayerNorm(out_channels)
        self.dropout = nn.Dropout(dropout)

        # Residual projection when channel sizes differ
        self.residual_proj: nn.Module
        if in_channels != out_channels:
            self.residual_proj = nn.Linear(in_channels, out_channels, bias=False)
        else:
            self.residual_proj = nn.Identity()

    def forward(self, x: Tensor, edge_index: Tensor) -> Tensor:
        """
        Parameters
        ----------
        x:
            ``[B, T, N, C_in]``
        edge_index:
            ``[2, E]``

        Returns
        -------
        Tensor
            ``[B, T, N, C_out]``
        """
        residual = self.residual_proj(x)
        out = self.tcn1(x)
        out = self.spatial(out, edge_index)
        out = self.tcn2(out)
        out = self.norm(out + residual)
        return self.dropout(out)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _batch_edge_index(edge_index: Tensor, batch_size: int, num_nodes: int) -> Tensor:
    """
    Replicate a single-graph ``edge_index`` for a mini-batch by offsetting
    node indices.

    Returns a ``[2, B * E]`` tensor where each copy has indices shifted by
    ``b * num_nodes``.
    """
    device = edge_index.device
    offsets = torch.arange(batch_size, device=device) * num_nodes   # [B]
    # edge_index: [2, E]  → expand to [2, E, B]
    ei_exp = edge_index.unsqueeze(-1).expand(-1, -1, batch_size)     # [2, E, B]
    offsets_exp = offsets.unsqueeze(0).unsqueeze(0).expand(2, edge_index.shape[1], -1)
    batched = (ei_exp + offsets_exp).reshape(2, -1)                  # [2, B*E]
    return batched
