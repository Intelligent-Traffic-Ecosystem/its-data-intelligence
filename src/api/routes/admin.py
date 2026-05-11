import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.event_bus import bus
from api.websocket import manager
from shared.config import settings
from shared.db import get_db
from shared.models import AdminThreshold, AuditLog, CameraRegistry, MonitoringZone
from shared.schemas import (
    BroadcastNotification,
    CameraRegistryCreate,
    CameraRegistryOut,
    CameraRegistryUpdate,
    Thresholds,
    ZoneCreate,
    ZoneOut,
    ZoneUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminActor:
    def __init__(self, actor_id: str):
        self.actor_id = actor_id


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ")
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_admin_user: Annotated[str | None, Header(alias="X-Admin-User")] = None,
) -> AdminActor:
    if not settings.admin_api_key or settings.admin_api_key == "change-me":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin API key is not configured",
        )

    token = x_admin_token or _extract_bearer_token(authorization)
    if token != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )

    actor_id = x_admin_user or "admin"
    return AdminActor(actor_id=actor_id)


def _log_audit(
    db: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    payload: dict | None,
) -> None:
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=json.dumps(payload) if payload is not None else None,
    )
    db.add(entry)
    logger.info(
        "admin_action",
        extra={
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
        },
    )


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


def _camera_to_out(camera: CameraRegistry) -> CameraRegistryOut:
    return CameraRegistryOut(
        id=camera.id,
        camera_id=camera.camera_id,
        name=camera.name,
        latitude=camera.latitude,
        longitude=camera.longitude,
        road_segment=camera.road_segment,
        description=camera.description,
        created_at=camera.created_at,
        updated_at=camera.updated_at,
    )


@router.get("/thresholds", response_model=Thresholds)
def get_thresholds(
    db: Session = Depends(get_db),
    _: AdminActor = Depends(require_admin),
):
    row = (
        db.execute(select(AdminThreshold).order_by(AdminThreshold.id.asc()).limit(1))
        .scalar_one_or_none()
    )
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
    row = (
        db.execute(select(AdminThreshold).order_by(AdminThreshold.id.asc()).limit(1))
        .scalar_one_or_none()
    )
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

    _log_audit(
        db,
        actor.actor_id,
        "thresholds.update",
        "admin_thresholds",
        str(row.id),
        payload.model_dump(),
    )
    db.commit()
    db.refresh(row)

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

    _log_audit(
        db,
        actor.actor_id,
        "zones.create",
        "monitoring_zones",
        str(zone.id),
        {"name": payload.name},
    )
    db.commit()
    db.refresh(zone)

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

    _log_audit(
        db,
        actor.actor_id,
        "zones.update",
        "monitoring_zones",
        str(zone.id),
        {"name": payload.name},
    )
    db.commit()
    db.refresh(zone)

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

    _log_audit(
        db,
        actor.actor_id,
        "zones.delete",
        "monitoring_zones",
        str(zone.id),
        {"name": zone.name},
    )
    db.commit()


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

    await manager.broadcast_to_operators(message)

    # Also fan-out via the unified /ws/events channel (#35).
    bus.publish("admin_broadcast", message)

    _log_audit(
        db,
        actor.actor_id,
        "notifications.broadcast",
        "operator_sessions",
        None,
        message,
    )
    db.commit()

    return {"status": "queued", "recipients": len(manager.operator_active)}


@router.get("/cameras", response_model=list[CameraRegistryOut])
def list_camera_registry(
    db: Session = Depends(get_db),
    _: AdminActor = Depends(require_admin),
):
    rows = db.execute(select(CameraRegistry).order_by(CameraRegistry.id.asc())).scalars().all()
    return [_camera_to_out(row) for row in rows]


@router.post("/cameras", response_model=CameraRegistryOut, status_code=status.HTTP_201_CREATED)
def create_camera_registry(
    payload: CameraRegistryCreate,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    existing = (
        db.execute(
            select(CameraRegistry).where(CameraRegistry.camera_id == payload.camera_id)
        ).scalar_one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="camera_id already exists"
        )

    row = CameraRegistry(**payload.model_dump())
    db.add(row)
    _log_audit(
        db, actor.actor_id, "cameras.create", "cameras", payload.camera_id, payload.model_dump()
    )
    db.commit()
    db.refresh(row)
    return _camera_to_out(row)


@router.put("/cameras/{camera_id}", response_model=CameraRegistryOut)
def update_camera_registry(
    camera_id: str,
    payload: CameraRegistryUpdate,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    row = (
        db.execute(select(CameraRegistry).where(CameraRegistry.camera_id == camera_id))
        .scalar_one_or_none()
    )
    updates = payload.model_dump(exclude_unset=True)
    if row is None:
        lat = updates.get("latitude")
        lng = updates.get("longitude")
        if lat is None or lng is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Camera not found. Provide latitude/longitude (or lat/lng) to create.",
            )
        row = CameraRegistry(
            camera_id=camera_id,
            name=updates.get("name") or camera_id,
            latitude=lat,
            longitude=lng,
            road_segment=updates.get("road_segment"),
            description=updates.get("description"),
        )
        db.add(row)
    else:
        for key, value in updates.items():
            setattr(row, key, value)

    _log_audit(db, actor.actor_id, "cameras.update", "cameras", camera_id, updates)
    db.commit()
    db.refresh(row)
    return _camera_to_out(row)


@router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera_registry(
    camera_id: str,
    db: Session = Depends(get_db),
    actor: AdminActor = Depends(require_admin),
):
    row = (
        db.execute(select(CameraRegistry).where(CameraRegistry.camera_id == camera_id))
        .scalar_one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    db.delete(row)
    _log_audit(db, actor.actor_id, "cameras.delete", "cameras", camera_id, None)
    db.commit()
