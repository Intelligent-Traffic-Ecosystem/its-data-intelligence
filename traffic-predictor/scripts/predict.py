"""
Real-time prediction entry point.

Supports two modes:

``api``
    Start the FastAPI HTTP server on the configured host/port.
    Exposes REST endpoints for on-demand forecasts and congestion queries.

``stream``
    Start the Kafka consumer loop.  Listens on the ``traffic.metrics`` topic,
    triggers predictions on each incoming window, and publishes results to
    ``traffic.predictions``.

``once``
    Run a single prediction cycle, print results to stdout, and exit.
    Useful for smoke-testing the trained model.

Usage::

    python scripts/predict.py api
    python scripts/predict.py stream
    python scripts/predict.py once [--camera cam-001] [--lane 1]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.shared.logging_setup import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ST-GCN real-time traffic prediction runner."
    )
    parser.add_argument(
        "mode",
        choices=["api", "stream", "once"],
        help="Execution mode",
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--camera", default=None, help="Filter output to a specific camera_id (once mode)")
    parser.add_argument("--lane", type=int, default=None, help="Filter output to a specific lane_id (once mode)")
    return parser.parse_args()


def run_api(cfg) -> None:
    import uvicorn

    logging.getLogger(__name__).info(
        "Starting prediction API on %s:%d", cfg.api.host, cfg.api.port
    )
    uvicorn.run(
        "src.api.main:app",
        host=cfg.api.host,
        port=cfg.api.port,
        log_level=cfg.api.log_level,
        reload=False,
    )


def run_stream(cfg) -> None:
    from src.streaming.kafka_consumer import PredictionStreamConsumer

    consumer = PredictionStreamConsumer(cfg)
    consumer.run()


def run_once(cfg, camera_filter: str | None, lane_filter: int | None) -> None:
    from src.data.graph_builder import build_graph
    from src.inference.predictor import TrafficPredictor

    logger = logging.getLogger(__name__)
    graph = build_graph(cfg)
    predictor = TrafficPredictor(config=cfg, graph=graph)

    logger.info("Running single prediction cycle...")
    forecasts = predictor.predict()

    if not forecasts:
        logger.warning("No forecasts produced — check DB connectivity and data availability.")
        return

    for fc in forecasts:
        if camera_filter and fc.camera_id != camera_filter:
            continue
        if lane_filter is not None and fc.lane_id != lane_filter:
            continue

        print(
            json.dumps(
                {
                    "camera_id": fc.camera_id,
                    "lane_id": fc.lane_id,
                    "congestion_start": fc.congestion_start.isoformat() if fc.congestion_start else None,
                    "congestion_end": fc.congestion_end.isoformat() if fc.congestion_end else None,
                    "peak_congestion_probability": round(fc.peak_congestion_probability, 4),
                    "next_5min_counts": fc.predicted_vehicle_counts[:60],
                    "next_5min_prob": [round(p, 3) for p in fc.congestion_probabilities[:60]],
                    "next_5min_levels": fc.congestion_levels[:60],
                },
                indent=2,
            )
        )


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    configure_logging(cfg.logging.level, cfg.logging.format)

    if args.mode == "api":
        run_api(cfg)
    elif args.mode == "stream":
        run_stream(cfg)
    elif args.mode == "once":
        run_once(cfg, args.camera, args.lane)


if __name__ == "__main__":
    main()
