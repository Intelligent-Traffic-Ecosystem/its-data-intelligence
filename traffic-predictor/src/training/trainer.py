"""
Training orchestrator for the ST-GCN model.

Responsibilities
----------------
* Split dataset into train / val / test splits.
* Run mini-batch gradient-descent training with a configurable LR scheduler.
* Early stopping on validation loss with best-model checkpointing.
* Per-epoch logging of train/val losses and evaluation metrics.
* Restores the best checkpoint at the end of training.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any

import torch
from torch import Tensor
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR, StepLR, _LRScheduler
from torch.utils.data import DataLoader, Subset, random_split

from src.config import Config, get_config
from src.data.dataset import TrafficGraphDataset
from src.data.graph_builder import LaneGraph
from src.model.stgcn import STGCN, STGCNOutput, compute_loss
from src.training.metrics import compute_all_metrics

logger = logging.getLogger(__name__)


def _collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Stack dataset items into a mini-batch; keep edge_index from first item."""
    keys = batch[0].keys()
    out: dict[str, Any] = {}
    for k in keys:
        if k == "edge_index":
            out[k] = batch[0][k]  # graph topology is identical for all items
        else:
            out[k] = torch.stack([item[k] for item in batch])
    return out


class Trainer:
    """
    Full training loop for the ST-GCN model.

    Parameters
    ----------
    model:
        Initialised :class:`~src.model.stgcn.STGCN` instance.
    dataset:
        Full :class:`~src.data.dataset.TrafficGraphDataset`.
    graph:
        :class:`~src.data.graph_builder.LaneGraph` (for bookkeeping).
    config:
        App config. Defaults to the global singleton.
    device:
        Torch device string (e.g. ``"cpu"``, ``"cuda"``).
    """

    def __init__(
        self,
        model: STGCN,
        dataset: TrafficGraphDataset,
        graph: LaneGraph,
        config: Config | None = None,
        device: str | None = None,
    ) -> None:
        self._cfg = config or get_config()
        self._tcfg = self._cfg.training
        self._model = model
        self._graph = graph
        self._device = torch.device(device or self._cfg.inference.device)
        self._model.to(self._device)

        self._train_loader, self._val_loader, self._test_loader = self._split(dataset)
        self._optimizer = AdamW(
            model.parameters(),
            lr=self._tcfg.learning_rate,
            weight_decay=self._tcfg.weight_decay,
        )
        self._scheduler = self._build_scheduler()
        self._best_val_loss = math.inf
        self._patience_counter = 0
        self._checkpoint_path = (
            Path(self._tcfg.checkpoint_dir) / "best_model.pt"
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def train(self) -> dict[str, float]:
        """
        Run the full training loop.

        Returns
        -------
        dict
            Final test-set metrics.
        """
        logger.info(
            "Training start — %d epochs  device=%s  "
            "train=%d  val=%d  test=%d",
            self._tcfg.epochs,
            self._device,
            len(self._train_loader.dataset),
            len(self._val_loader.dataset),
            len(self._test_loader.dataset),
        )

        for epoch in range(1, self._tcfg.epochs + 1):
            t0 = time.time()
            train_loss, train_breakdown = self._run_epoch(self._train_loader, train=True)
            val_loss, val_breakdown = self._run_epoch(self._val_loader, train=False)
            elapsed = time.time() - t0

            self._scheduler.step()

            lr = self._optimizer.param_groups[0]["lr"]
            logger.info(
                "Epoch %d/%d | train_loss=%.4f  val_loss=%.4f  lr=%.2e  %.1fs",
                epoch, self._tcfg.epochs, train_loss, val_loss, lr, elapsed,
            )
            if epoch % self._tcfg.log_interval == 0:
                logger.info("  train breakdown: %s", train_breakdown)
                logger.info("  val   breakdown: %s", val_breakdown)

            if val_loss < self._best_val_loss:
                self._best_val_loss = val_loss
                self._patience_counter = 0
                self._model.save(self._checkpoint_path)
                logger.info("  ✓ Best model saved (val_loss=%.4f)", val_loss)
            else:
                self._patience_counter += 1
                if self._patience_counter >= self._tcfg.early_stopping_patience:
                    logger.info(
                        "Early stopping after %d epochs without improvement.",
                        self._tcfg.early_stopping_patience,
                    )
                    break

        # Restore best weights and evaluate on test set
        if self._checkpoint_path.exists():
            checkpoint = torch.load(self._checkpoint_path, map_location=self._device, weights_only=False)
            self._model.load_state_dict(checkpoint["state_dict"])
            logger.info("Best model restored from %s", self._checkpoint_path)

        test_metrics = self._evaluate_test()
        logger.info("Test metrics: %s", test_metrics)
        return test_metrics

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_epoch(
        self, loader: DataLoader, train: bool
    ) -> tuple[float, dict[str, float]]:
        self._model.train(train)
        total_loss = 0.0
        breakdown_acc: dict[str, float] = {}
        n_batches = 0

        ctx_manager = torch.enable_grad if train else torch.no_grad
        with ctx_manager():
            for batch in loader:
                x = batch["x"].to(self._device)
                edge_index = batch["edge_index"].to(self._device)
                targets = {
                    k: v.to(self._device)
                    for k, v in batch.items()
                    if k.startswith("y_")
                }

                if train:
                    self._optimizer.zero_grad()

                output: STGCNOutput = self._model(x, edge_index)
                loss, breakdown = compute_loss(output, targets, self._cfg)

                if train:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(self._model.parameters(), max_norm=1.0)
                    self._optimizer.step()

                total_loss += loss.item()
                for k, v in breakdown.items():
                    breakdown_acc[k] = breakdown_acc.get(k, 0.0) + v.item()
                n_batches += 1

        avg_loss = total_loss / max(n_batches, 1)
        avg_breakdown = {k: v / max(n_batches, 1) for k, v in breakdown_acc.items()}
        return avg_loss, avg_breakdown

    def _evaluate_test(self) -> dict[str, float]:
        self._model.eval()
        preds_count, preds_prob, preds_level = [], [], []
        ys_count, ys_prob, ys_level = [], [], []

        with torch.no_grad():
            for batch in self._test_loader:
                x = batch["x"].to(self._device)
                edge_index = batch["edge_index"].to(self._device)
                output = self._model(x, edge_index)

                if output.vehicle_count is not None:
                    preds_count.append(output.vehicle_count.cpu())
                    ys_count.append(batch["y_vehicle_count"])
                if output.congestion_prob is not None:
                    preds_prob.append(output.congestion_prob.cpu())
                    ys_prob.append(batch["y_congestion_prob"])
                if output.congestion_level is not None:
                    preds_level.append(output.congestion_level.cpu())
                    ys_level.append(batch["y_congestion_level"])

        def _cat(lst: list[Tensor]) -> Tensor | None:
            return torch.cat(lst, dim=0) if lst else None

        return compute_all_metrics(
            pred_count=_cat(preds_count),
            pred_prob=_cat(preds_prob),
            pred_level=_cat(preds_level),
            y_count=_cat(ys_count),
            y_prob=_cat(ys_prob),
            y_level=_cat(ys_level),
        )

    def _split(
        self, dataset: TrafficGraphDataset
    ) -> tuple[DataLoader, DataLoader, DataLoader]:
        n = len(dataset)
        n_test = max(1, int(n * self._tcfg.test_fraction))
        n_val = max(1, int(n * self._tcfg.val_fraction))
        n_train = n - n_val - n_test

        if n_train <= 0:
            raise ValueError(
                f"Dataset too small ({n} samples) for train/val/test split."
            )

        train_ds, val_ds, test_ds = random_split(
            dataset,
            [n_train, n_val, n_test],
            generator=torch.Generator().manual_seed(42),
        )

        bs = self._tcfg.batch_size
        return (
            DataLoader(train_ds, batch_size=bs, shuffle=True,  collate_fn=_collate),
            DataLoader(val_ds,   batch_size=bs, shuffle=False, collate_fn=_collate),
            DataLoader(test_ds,  batch_size=bs, shuffle=False, collate_fn=_collate),
        )

    def _build_scheduler(self) -> _LRScheduler:
        scfg = self._tcfg.lr_scheduler
        if scfg.type == "cosine":
            return CosineAnnealingLR(
                self._optimizer, T_max=scfg.T_max, eta_min=scfg.eta_min
            )
        elif scfg.type == "step":
            return StepLR(self._optimizer, step_size=scfg.T_max, gamma=0.1)
        else:
            # No-op scheduler
            return StepLR(self._optimizer, step_size=10_000, gamma=1.0)
