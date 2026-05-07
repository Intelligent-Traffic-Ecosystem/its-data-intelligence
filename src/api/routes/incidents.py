from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.db import get_db
from shared.models import TrafficMetric
from shared.schemas import IncidentOutput

router = APIRouter(prefix="/api")


@router.get("/map/incidents", response_model=list[IncidentOutput])
def get_map_incidents(db: Session = Depends(get_db)):
    """Return active incident markers with severity and location."""
    
    # 1. Get the latest metric row for each camera
    stmt = (
        select(TrafficMetric)
        .distinct(TrafficMetric.camera_id)
        .where(TrafficMetric.lane_id.is_(None))
        .order_by(TrafficMetric.camera_id, TrafficMetric.window_start.desc())
    )
    
    rows = db.execute(stmt).scalars().all()
    
    incidents = []
    for row in rows:
        # 2. Consider HIGH or SEVERE congestion as an active incident
        if row.congestion_level in ("SEVERE", "HIGH"):
            # Generate a dynamic description based on the stats
            desc = f"{row.congestion_level.capitalize()} congestion detected."
            if row.stopped_ratio and row.stopped_ratio > 0.5:
                desc += f" Heavy traffic stop ({int(row.stopped_ratio * 100)}% vehicles stationary)."
            elif row.avg_speed_kmh is not None and row.avg_speed_kmh < 10:
                desc += f" Traffic is crawling at {row.avg_speed_kmh:.1f} km/h."
                
            incidents.append(
                IncidentOutput(
                    incident_id=f"inc_{row.camera_id}_{int(row.window_start.timestamp())}",
                    camera_id=row.camera_id,
                    latitude=None,   # We don't have geospatial data in the DB yet
                    longitude=None,  # We don't have geospatial data in the DB yet
                    severity=row.congestion_level,
                    description=desc,
                    timestamp=row.window_start,
                )
            )
            
    return incidents
