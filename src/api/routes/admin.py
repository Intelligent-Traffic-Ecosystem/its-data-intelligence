import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.websocket import manager
from shared.config import settings
from shared.db import get_db
from shared.models import AdminThreshold, AuditLog, MonitoringZone
from shared.schemas import BroadcastNotification, Thresholds, ZoneCreate, ZoneOut, ZoneUpdate

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

    return AdminActor(actor_id="admin")


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

    _log_audit(
        db,
        actor.actor_id,
        "notifications.broadcast",
        "operator_sessions",
        None,
        message,
    )

    return {"status": "queued", "recipients": len(manager.operator_active)}
