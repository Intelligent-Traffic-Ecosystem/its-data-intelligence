import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from api.prometheus import (
    PROCESSING_ERRORS,
    REQUEST_COUNT,
    REQUEST_LATENCY,
)
from api.prometheus import router as prom_router
from api.routes import (
    admin,
    alerts,
    analytics,
    cameras,
    congestion,
    dashboard,
    health,
    metrics,
    predict,
)
from api.websocket import router as ws_router, start_event_producers
from shared.db import SessionLocal, engine
from shared.logging_setup import configure_logging
from shared.models import Base
from shared.threshold_loader import load_thresholds_from_db

configure_logging("b2-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("b2-api")
    Base.metadata.create_all(bind=engine)
    # Load admin thresholds from database
    with SessionLocal() as db:
        load_thresholds_from_db(db)
    # Spawn real-time event producers (#35)
    start_event_producers()
    yield


app = FastAPI(
    title="B2 Data & Intelligence API",
    description="Traffic analytics API for the Intelligent Traffic System",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    start = time.perf_counter()  #start time
    endpoint = request.url.path
    try:
        response = await call_next(request)
    except Exception:
        PROCESSING_ERRORS.labels(type="api_unhandled").inc()
        REQUEST_COUNT.labels(
            method=request.method, endpoint=endpoint, status="500"
        ).inc()
        raise
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(time.perf_counter() - start)
    REQUEST_COUNT.labels(
        method=request.method, endpoint=endpoint, status=str(response.status_code)
    ).inc()
    return response


app.include_router(cameras.router, tags=["cameras"])
app.include_router(metrics.router, tags=["metrics"])
app.include_router(congestion.router, tags=["congestion"])
app.include_router(dashboard.router, tags=["dashboard"])
app.include_router(health.router, tags=["health"])
app.include_router(alerts.router, tags=["alerts"])
app.include_router(analytics.router, tags=["analytics"])
app.include_router(admin.router, tags=["admin"])
app.include_router(predict.router)
app.include_router(ws_router)
app.include_router(prom_router)
