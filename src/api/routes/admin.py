import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import AdminActor, require_admin
from api.auth import log_audit as _log_audit
from api.websocket import manager
from shared.config import settings
from shared.db import get_db
from shared.models import AdminThreshold, Camera, MonitoringZone
from shared.schemas import (
    BroadcastNotification,
    CameraCreate,
    CameraOut,
    CameraUpdate,
    Thresholds,
    ZoneCreate,
    ZoneOut,
    ZoneUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


def _zone_to_out(zone: MonitoringZone) -> ZoneOut:
    return ZoneOut(
        id=zone.id,
        name=zone.name,
        description=zone.description,
        coordinates=json.loads(zone.polygon_wgs84),
        created_at=zone.created_at,
        updated_at=zone.updated_at,
    )


def _normalize_coordinates(points: list[dict]) -> list[dict]:
    if not points:
        return points
    if points[0] != points[-1]:
        return points + [points[0]]
    return points


@router.get("/thresholds", response_model=Thresholds)
def get_thresholds(
    db: Session = Depends(get_db),
    _: AdminActor = Depends(require_admin),
):
    row = db.execute(select(AdminThreshold).limit(1)).scalar_one_or_none()
    if row is None:
        row = AdminThreshold(
            congestion_threshold_low=settings.congestion_threshold_low,
            congestion_threshold_moderate=settings.congestion_threshold_moderate,
            congestion_threshold_high=settings.congestion_threshold_high,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    return Thresholds(
        congestion_threshold_low=row.congestion_threshold_low,
        congestion_threshold_moderate=row.congestion_threshold_moderate,
        congestion_threshold_high=row.congestion_threshold_high,
    )


@router.put("/thresholds", response_model=Thresholds)
def update_thresholds(
    payload: Thresholds,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    row = db.execute(select(AdminThreshold).limit(1)).scalar_one_or_none()
    if row is None:
        row = AdminThreshold(
            congestion_threshold_low=payload.congestion_threshold_low,
            congestion_threshold_moderate=payload.congestion_threshold_moderate,
            congestion_threshold_high=payload.congestion_threshold_high,
        )
        db.add(row)
    else:
        row.congestion_threshold_low = payload.congestion_threshold_low
        row.congestion_threshold_moderate = payload.congestion_threshold_moderate
        row.congestion_threshold_high = payload.congestion_threshold_high

    db.commit()
    db.refresh(row)

    _log_audit(
        db,
        actor.actor_id,
        "thresholds.update",
        "admin_thresholds",
        str(row.id),
        payload.model_dump(),
    )

    return Thresholds(
        congestion_threshold_low=row.congestion_threshold_low,
        congestion_threshold_moderate=row.congestion_threshold_moderate,
        congestion_threshold_high=row.congestion_threshold_high,
    )


@router.get("/zones", response_model=list[ZoneOut])
def list_zones(
    db: Session = Depends(get_db),
    _: AdminActor = Depends(require_admin),
):
    zones = (
        db.execute(select(MonitoringZone).order_by(MonitoringZone.id.asc()))
        .scalars()
        .all()
    )
    return [_zone_to_out(zone) for zone in zones]


@router.post("/zones", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
def create_zone(
    payload: ZoneCreate,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    coordinates = _normalize_coordinates(
        [point.model_dump() for point in payload.coordinates]
    )

    zone = MonitoringZone(
        name=payload.name,
        description=payload.description,
        polygon_wgs84=json.dumps(coordinates),
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)

    _log_audit(
        db,
        actor.actor_id,
        "zones.create",
        "monitoring_zones",
        str(zone.id),
        {"name": payload.name},
    )

    return _zone_to_out(zone)


@router.put("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(
    zone_id: int,
    payload: ZoneUpdate,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    zone = db.get(MonitoringZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")

    coordinates = _normalize_coordinates(
        [point.model_dump() for point in payload.coordinates]
    )

    zone.name = payload.name
    zone.description = payload.description
    zone.polygon_wgs84 = json.dumps(coordinates)
    db.commit()
    db.refresh(zone)

    _log_audit(
        db,
        actor.actor_id,
        "zones.update",
        "monitoring_zones",
        str(zone.id),
        {"name": payload.name},
    )

    return _zone_to_out(zone)


@router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    zone = db.get(MonitoringZone, zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")

    db.delete(zone)
    db.commit()

    _log_audit(
        db,
        actor.actor_id,
        "zones.delete",
        "monitoring_zones",
        str(zone.id),
        {"name": zone.name},
    )


def _camera_to_out(cam: Camera) -> CameraOut:
    return CameraOut(
        id=cam.id,
        camera_id=cam.camera_id,
        name=cam.name,
        latitude=cam.latitude,
        longitude=cam.longitude,
        road_segment=cam.road_segment,
        description=cam.description,
        created_at=cam.created_at,
        updated_at=cam.updated_at,
    )


@router.get("/cameras", response_model=list[CameraOut])
def list_cameras_registry(
    db: Session = Depends(get_db),
    _: AdminActor = Depends(require_admin),
):
    rows = (
        db.execute(select(Camera).order_by(Camera.camera_id.asc())).scalars().all()
    )
    return [_camera_to_out(r) for r in rows]


@router.post(
    "/cameras",
    response_model=CameraOut,
    status_code=status.HTTP_201_CREATED,
)
def create_camera(
    payload: CameraCreate,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    existing = db.execute(
        select(Camera).where(Camera.camera_id == payload.camera_id)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Camera {payload.camera_id} already exists",
        )

    cam = Camera(
        camera_id=payload.camera_id,
        name=payload.name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        road_segment=payload.road_segment,
        description=payload.description,
    )
    db.add(cam)
    db.commit()
    db.refresh(cam)

    _log_audit(
        db,
        actor.actor_id,
        "cameras.create",
        "cameras",
        str(cam.id),
        payload.model_dump(),
    )
    return _camera_to_out(cam)


@router.put("/cameras/{camera_id}", response_model=CameraOut)
def update_camera(
    camera_id: str,
    payload: CameraUpdate,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    cam = db.execute(
        select(Camera).where(Camera.camera_id == camera_id)
    ).scalar_one_or_none()
    if cam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(cam, k, v)
    db.commit()
    db.refresh(cam)

    _log_audit(
        db,
        actor.actor_id,
        "cameras.update",
        "cameras",
        str(cam.id),
        updates,
    )
    return _camera_to_out(cam)


@router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    cam = db.execute(
        select(Camera).where(Camera.camera_id == camera_id)
    ).scalar_one_or_none()
    if cam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    db.delete(cam)
    db.commit()

    _log_audit(
        db,
        actor.actor_id,
        "cameras.delete",
        "cameras",
        str(cam.id),
        {"camera_id": camera_id},
    )


@router.post("/notifications/broadcast", status_code=status.HTTP_202_ACCEPTED)
async def broadcast_notification(
    payload: BroadcastNotification,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    message = {
        "type": "notification",
        "severity": payload.severity,
        "title": payload.title,
        "message": payload.message,
    }

    await manager.broadcast(message)

    _log_audit(
        db,
        actor.actor_id,
        "notifications.broadcast",
        "operator_sessions",
        None,
        message,
    )

    return {"status": "queued", "recipients": len(manager.active)}
