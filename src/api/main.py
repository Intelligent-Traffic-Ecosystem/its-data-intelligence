from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.db import engine
from shared.models import Base
from api.routes import cameras, congestion, health, metrics
from api.websocket import router as ws_router
from api.prometheus import router as prom_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables exist on startup
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="B2 Data & Intelligence API",
    description="Traffic analytics API for the Intelligent Traffic System",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(cameras.router, tags=["cameras"])
app.include_router(metrics.router, tags=["metrics"])
app.include_router(congestion.router, tags=["congestion"])
app.include_router(health.router, tags=["health"])
app.include_router(ws_router)
app.include_router(prom_router)
