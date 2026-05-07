import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from api.prometheus import (
    PROCESSING_ERRORS,
    REQUEST_COUNT,
    REQUEST_LATENCY,
)
from api.prometheus import router as prom_router
from api.routes import admin, alerts, cameras, congestion, health, metrics
from api.websocket import router as ws_router
from shared.db import engine
from shared.logging_setup import configure_logging
from shared.models import Base

configure_logging("b2-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("b2-api")
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="B2 Data & Intelligence API",
    description="Traffic analytics API for the Intelligent Traffic System",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    start = time.perf_counter()
    endpoint = request.url.path
    try:
        response = await call_next(request)
    except Exception:
        PROCESSING_ERRORS.labels(type="api_unhandled").inc()
        REQUEST_COUNT.labels(method=request.method, endpoint=endpoint, status="500").inc()
        raise
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(time.perf_counter() - start)
    REQUEST_COUNT.labels(
        method=request.method, endpoint=endpoint, status=str(response.status_code)
    ).inc()
    return response


app.include_router(cameras.router, tags=["cameras"])
app.include_router(metrics.router, tags=["metrics"])
app.include_router(congestion.router, tags=["congestion"])
app.include_router(health.router, tags=["health"])
app.include_router(admin.router)
app.include_router(ws_router)
app.include_router(prom_router)
app.include_router(alerts.router, tags=["alerts"])
