"""
FastAPI application for the ST-GCN traffic prediction service.

Startup sequence
----------------
1. Load config.
2. Build the lane graph.
3. Load the trained ST-GCN model from checkpoint.
4. Initialise :class:`~src.inference.predictor.TrafficPredictor`.
5. Mount all API routes.

The app is intentionally stateless across requests — the predictor fetches
fresh DB data on every call so that stale in-memory caches are never served.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.config import get_config
from src.data.graph_builder import build_graph
from src.inference.predictor import TrafficPredictor
from src.shared.logging_setup import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build all heavy resources once at startup and clean up on shutdown."""
    cfg = get_config()
    configure_logging(cfg.logging.level, cfg.logging.format)

    logger.info("Starting ST-GCN prediction service...")
    graph = build_graph(cfg)
    predictor = TrafficPredictor(config=cfg, graph=graph)
    app.state.predictor = predictor
    logger.info("Prediction service ready.")

    yield

    logger.info("Shutting down prediction service.")


def create_app() -> FastAPI:
    cfg = get_config()

    app = FastAPI(
        title="ST-GCN Traffic Forecasting API",
        description=(
            "Lane-level traffic volume and congestion forecasting "
            "using Spatio-Temporal Graph Convolutional Networks."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api/v1")

    return app


app = create_app()


if __name__ == "__main__":
    cfg = get_config()
    uvicorn.run(
        "src.api.main:app",
        host=cfg.api.host,
        port=cfg.api.port,
        log_level=cfg.api.log_level,
        reload=False,
    )
