"""
Evaluation metrics for the multi-task ST-GCN model.

All functions accept PyTorch tensors and return plain Python floats for
compatibility with logging and progress-bar libraries.
"""

from __future__ import annotations

import torch
from torch import Tensor


def mae(pred: Tensor, target: Tensor) -> float:
    """Mean Absolute Error (regression)."""
    return torch.mean(torch.abs(pred - target)).item()


def rmse(pred: Tensor, target: Tensor) -> float:
    """Root Mean Squared Error (regression)."""
    return torch.sqrt(torch.mean((pred - target) ** 2)).item()


def mape(pred: Tensor, target: Tensor, eps: float = 1e-6) -> float:
    """Mean Absolute Percentage Error (regression)."""
    return torch.mean(torch.abs((pred - target) / (target.abs() + eps))).item() * 100


def binary_accuracy(prob: Tensor, target: Tensor, threshold: float = 0.5) -> float:
    """Binary classification accuracy for congestion probability head."""
    predicted = (prob >= threshold).long()
    labels = (target >= threshold).long()
    return (predicted == labels).float().mean().item()


def multiclass_accuracy(logits: Tensor, labels: Tensor) -> float:
    """
    Top-1 accuracy for the congestion-level classification head.

    Parameters
    ----------
    logits:
        Raw logits ``[B, T_out, N, C]`` or any shape ending in C.
    labels:
        Integer class indices, shape matching logits except last dim.
    """
    preds = logits.argmax(dim=-1)
    return (preds == labels).float().mean().item()


def compute_all_metrics(
    pred_count: Tensor | None,
    pred_prob: Tensor | None,
    pred_level: Tensor | None,
    y_count: Tensor | None,
    y_prob: Tensor | None,
    y_level: Tensor | None,
) -> dict[str, float]:
    """Aggregate all available metrics into a flat dict suitable for logging."""
    result: dict[str, float] = {}

    if pred_count is not None and y_count is not None:
        result["count_mae"]  = mae(pred_count, y_count)
        result["count_rmse"] = rmse(pred_count, y_count)
        result["count_mape"] = mape(pred_count, y_count)

    if pred_prob is not None and y_prob is not None:
        result["prob_acc"]   = binary_accuracy(pred_prob, y_prob)

    if pred_level is not None and y_level is not None:
        result["level_acc"]  = multiclass_accuracy(pred_level, y_level)

    return result
