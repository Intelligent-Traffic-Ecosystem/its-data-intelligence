"""
Training entry point.

Usage::

    # From the traffic-predictor/ directory:
    python scripts/train.py

    # Override config path:
    python scripts/train.py --config /path/to/config.yaml

    # Resume from existing checkpoint (skip if already trained):
    python scripts/train.py --device cuda

Environment variable overrides::

    STGCN_DATABASE__URL=postgresql://... python scripts/train.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make src importable when running from project root or scripts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.data.db_loader import TrafficMetricsLoader
from src.data.graph_builder import build_graph
from src.data.dataset import TrafficGraphDataset
from src.model.stgcn import STGCN
from src.training.trainer import Trainer
from src.shared.logging_setup import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train the ST-GCN lane-level traffic forecasting model."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: auto-discovered)",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Torch device: cpu | cuda | mps (overrides config)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=None,
        help="Number of training epochs (overrides config)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="How many days of traffic_metrics history to load (default: 30)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    cfg = load_config(args.config)
    configure_logging(cfg.logging.level, cfg.logging.format)
    logger = logging.getLogger(__name__)

    # Apply CLI overrides
    if args.device:
        cfg.inference.device = args.device
    if args.epochs:
        cfg.training.epochs = args.epochs

    logger.info("=== ST-GCN Training ===")
    logger.info("Config: %s", args.config or "auto-discovered config.yaml")
    logger.info("Device: %s", cfg.inference.device)
    logger.info("Lookback: %d days", args.lookback_days)

    # Build graph
    graph = build_graph(cfg)
    logger.info("Graph: %d nodes, %d edges", graph.num_nodes, graph.edge_index.shape[1])

    # Load data
    from datetime import datetime, timedelta, timezone
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=args.lookback_days)

    loader = TrafficMetricsLoader(cfg)
    df = loader.load_training_data(start=start, end=end)

    if df.empty:
        logger.error(
            "No data found in traffic_metrics for the requested period. "
            "Ensure the stream processor has been running and lane_id IS NOT NULL."
        )
        sys.exit(1)

    logger.info("Loaded %d rows of training data.", len(df))

    # Build dataset
    dataset = TrafficGraphDataset(df, graph, cfg)
    logger.info("Dataset: %d sliding windows.", len(dataset))

    # Build model
    model = STGCN(
        num_node_features=cfg.features.num_node_features,
        num_nodes=graph.num_nodes,
        config=cfg,
    )

    # Train
    trainer = Trainer(model, dataset, graph, cfg, device=cfg.inference.device)
    test_metrics = trainer.train()

    logger.info("=== Training complete ===")
    logger.info("Test metrics: %s", test_metrics)
    logger.info("Best checkpoint: %s", cfg.training.checkpoint_dir + "/best_model.pt")


if __name__ == "__main__":
    main()
