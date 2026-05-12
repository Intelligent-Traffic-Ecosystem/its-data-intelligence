from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficMetric
from shared.schemas import CameraInfo

router = APIRouter()


@router.get("/cameras", response_model=list[CameraInfo])
def list_cameras(db: Session = Depends(get_db)):
    """List all cameras that have reported data."""
    stmt = select(
        TrafficMetric.camera_id,
        func.max(TrafficMetric.window_end).label("last_seen"),
    ).where(TrafficMetric.lane_id.is_(None)).group_by(TrafficMetric.camera_id)

    rows = db.execute(stmt).all()
    return [CameraInfo(camera_id=row.camera_id, last_seen=row.last_seen) for row in rows]
