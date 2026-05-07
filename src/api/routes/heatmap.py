from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficMetric
from shared.schemas import HeatmapDataOutput

router = APIRouter(prefix="/api")


@router.get("/map/heatmap", response_model=list[HeatmapDataOutput])
def get_map_heatmap(db: Session = Depends(get_db)):
    """Deliver heatmap density data (latest metrics per active camera)."""
    
    # Use DISTINCT ON to get the latest metric row for each camera
    stmt = (
        select(TrafficMetric)
        .distinct(TrafficMetric.camera_id)
        .where(TrafficMetric.lane_id.is_(None))
        .order_by(TrafficMetric.camera_id, TrafficMetric.window_start.desc())
    )
    
    rows = db.execute(stmt).scalars().all()
    
    return [
        HeatmapDataOutput(
            camera_id=row.camera_id,
            latitude=None,   # We don't have geospatial data in the DB yet
            longitude=None,  # We don't have geospatial data in the DB yet
            vehicle_count=row.vehicle_count or 0,
            congestion_score=row.congestion_score or 0.0,
            congestion_level=row.congestion_level or "LOW",
        )
        for row in rows
    ]
