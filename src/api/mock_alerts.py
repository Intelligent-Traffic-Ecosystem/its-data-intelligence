"""
Mock alert generator for development / standalone mode.

Seeds the database with realistic alerts at startup and periodically
generates new ones so the alert section always has data to display.
"""

import asyncio
import json
import logging
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from shared.db import SessionLocal
from shared.models import AlertRecord

logger = logging.getLogger(__name__)

MOCK_CAMERAS = [
    {"id": "cam1", "road": "Galle Road"},
    {"id": "cam2", "road": "High Level Road"},
    {"id": "cam3", "road": "Kandy Road"},
    {"id": "cam4", "road": "Nugegoda Junction"},
    {"id": "cam5", "road": "Rajagiriya"},
    {"id": "cam6", "road": "Borella Junction"},
    {"id": "cam7", "road": "Maradana"},
    {"id": "cam8", "road": "Pettah Bus Terminal"},
]

_TEMPLATES = [
    {
        "alert_type": "CONGESTION",
        "severity": "WARNING",
        "congestion_level": "HIGH",
        "score_range": (60.0, 79.9),
        "title": "High congestion on {road}",
        "message": (
            "Vehicle density has risen sharply on {road}. "
            "{count} vehicles detected, average speed {speed} km/h. "
            "Congestion score {score:.1f}/100."
        ),
    },
    {
        "alert_type": "CONGESTION",
        "severity": "CRITICAL",
        "congestion_level": "CRITICAL",
        "score_range": (80.0, 94.9),
        "title": "Critical congestion at {road}",
        "message": (
            "Severe congestion on {road}. "
            "{count} vehicles with average speed {speed} km/h. "
            "Immediate operator attention recommended. Score {score:.1f}/100."
        ),
    },
    {
        "alert_type": "STOPPED_TRAFFIC",
        "severity": "CRITICAL",
        "congestion_level": "CRITICAL",
        "score_range": (90.0, 99.9),
        "title": "Traffic standstill detected at {road}",
        "message": (
            "Traffic has come to a near-complete stop at {road}. "
            "{count} vehicles affected. Average speed {speed} km/h."
        ),
    },
    {
        "alert_type": "INCIDENT",
        "severity": "EMERGENCY",
        "congestion_level": "CRITICAL",
        "score_range": (85.0, 99.9),
        "title": "Possible incident at {road}",
        "message": (
            "Unusual traffic pattern at {road} may indicate an incident. "
            "{count} vehicles in affected area. Emergency services may be required."
        ),
    },
    {
        "alert_type": "CONGESTION",
        "severity": "WARNING",
        "congestion_level": "MEDIUM",
        "score_range": (45.0, 62.0),
        "title": "Moderate congestion building on {road}",
        "message": (
            "Congestion is building on {road}. "
            "{count} vehicles, average speed {speed} km/h. Score {score:.1f}/100."
        ),
    },
]


def _make_alert(camera: dict, template: dict, triggered_at: datetime) -> AlertRecord:
    score = round(random.uniform(*template["score_range"]), 1)
    count = random.randint(15, 90)
    speed = round(random.uniform(3.0, 25.0), 1)
    road = camera["road"]
    return AlertRecord(
        severity=template["severity"],
        alert_type=template["alert_type"],
        camera_id=camera["id"],
        road_segment=road,
        title=template["title"].format(road=road),
        message=template["message"].format(road=road, count=count, speed=speed, score=score),
        congestion_level=template["congestion_level"],
        congestion_score=score,
        triggered_at=triggered_at,
        payload=json.dumps({"vehicle_count": count, "avg_speed_kmh": speed}),
    )


def seed_alerts(db: Session) -> None:
    """Create initial mock alerts if the table is empty."""
    from sqlalchemy import select, func as sqlfunc
    count = db.execute(select(sqlfunc.count()).select_from(AlertRecord)).scalar()
    if count and count > 0:
        logger.info("mock_alerts: %d alerts already in DB, skipping seed", count)
        return

    logger.info("mock_alerts: seeding initial alerts into DB")
    now = datetime.now(UTC)
    records = []

    # Spread 15 alerts over the past 3 hours (10 unacknowledged, 5 acknowledged)
    for i in range(15):
        camera = random.choice(MOCK_CAMERAS)
        template = random.choice(_TEMPLATES)
        triggered_at = now - timedelta(minutes=random.randint(5, 180))
        alert = _make_alert(camera, template, triggered_at)

        if i < 5:
            # Make the first 5 acknowledged (so history section has data)
            ack_delay = random.randint(2, 30)
            alert.acknowledged_by = random.choice(["admin", "operator1", "supervisor"])
            alert.acknowledged_at = triggered_at + timedelta(minutes=ack_delay)

        records.append(alert)

    db.add_all(records)
    db.commit()
    logger.info("mock_alerts: seeded %d alerts", len(records))


def generate_new_alert(db: Session) -> AlertRecord:
    """Insert a single new mock alert and return it."""
    camera = random.choice(MOCK_CAMERAS)
    template = random.choice(_TEMPLATES)
    alert = _make_alert(camera, template, datetime.now(UTC))
    db.add(alert)
    db.commit()
    db.refresh(alert)
    logger.info(
        "mock_alerts: generated new alert id=%d camera=%s severity=%s",
        alert.id, alert.camera_id, alert.severity,
    )
    return alert


async def run_mock_alert_generator(interval_seconds: int = 45) -> None:
    """Background coroutine: insert a new alert every *interval_seconds*."""
    logger.info("mock_alerts: background generator started (interval=%ds)", interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            with SessionLocal() as db:
                generate_new_alert(db)
        except Exception:
            logger.exception("mock_alerts: failed to generate alert")
