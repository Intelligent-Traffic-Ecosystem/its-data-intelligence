"""Shared admin auth + audit-log helpers used by /api/admin and /api/alerts.

Auth model: header `X-Admin-Token` (or `Authorization: Bearer <token>`) must
match `settings.admin_api_key`. The actor's user id is taken from the
`X-Admin-User` header for audit logging; defaults to "admin".

This is a temporary in-app token until B4 ships an auth provider. Document it
in the API contract so B3 knows what headers to send.
"""

import json
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from shared.config import settings
from shared.models import AuditLog

logger = logging.getLogger(__name__)


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


AdminDep = Annotated[AdminActor, Depends(require_admin)]


def log_audit(
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
    db.commit()
    logger.info(
        "admin_action",
        extra={
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
        },
    )
