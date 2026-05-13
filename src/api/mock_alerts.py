"""
Mock alert generator for development / standalone mode.

Seeds the database with realistic alerts and smooth traffic metrics at startup,
and periodically generates new alerts so the UI always has fresh data.
"""

import asyncio
import json
import logging
import math
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from shared.db import SessionLocal
from shared.models import AlertRecord, TrafficMetric

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


def _congestion_score_at(hour: float, camera_index: int) -> float:
    """Return a smooth congestion score [0.0-1.0] for a given hour using a diurnal model.

    Two rush-hour peaks (8 am and 6 pm) with Gaussian envelopes sit on a low
    overnight baseline.  Each camera has a slight phase/amplitude offset so the
    cameras don't all peak identically.  Matches the 0-1 scale the stream
    processor writes so the frontend's ×100 display multiplication works correctly.
    """
    phase_shift = camera_index * 0.3   # hours
    amp_scale   = 1.0 - camera_index * 0.04

    morning_peak = 0.65 * amp_scale * math.exp(-0.5 * ((hour - (8.0 + phase_shift)) / 1.4) ** 2)
    evening_peak = 0.72 * amp_scale * math.exp(-0.5 * ((hour - (18.0 + phase_shift * 0.5)) / 1.6) ** 2)
    baseline = 0.12
    return min(1.0, max(0.0, baseline + morning_peak + evening_peak))


def _congestion_level(score: float) -> str:
    # Thresholds match congestion.py in the stream processor (0-1 scale)
    if score >= 0.80:
        return "SEVERE"
    if score >= 0.55:
        return "HIGH"
    if score >= 0.30:
        return "MODERATE"
    return "LOW"


def seed_traffic_metrics(db: Session) -> None:
    """Seed 48 hours of smooth diurnal traffic metrics if the table is empty."""
    from sqlalchemy import select, func as sqlfunc

    count = db.execute(select(sqlfunc.count()).select_from(TrafficMetric)).scalar()
    if count and count > 0:
        logger.info("mock_traffic: %d traffic_metrics rows already exist, skipping seed", count)
        return

    logger.info("mock_traffic: seeding 48 h of smooth traffic metrics")
    window_minutes = 5
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    # Round down to nearest 5-minute boundary
    now -= timedelta(minutes=now.minute % window_minutes)

    cameras = [c["id"] for c in MOCK_CAMERAS]
    camera_base_speed = {
        "cam1": 42.0, "cam2": 38.0, "cam3": 50.0, "cam4": 34.0,
        "cam5": 46.0, "cam6": 55.0, "cam7": 32.0, "cam8": 40.0,
    }

    records = []
    n_windows = int(48 * 60 / window_minutes)  # 48 hours of 5-min windows

    for cam_idx, cam_id in enumerate(cameras):
        base_speed = camera_base_speed.get(cam_id, 40.0)
        for w in range(n_windows):
            w_start = now - timedelta(minutes=(n_windows - w) * window_minutes)
            w_end   = w_start + timedelta(minutes=window_minutes)

            # Fractional hour including minutes
            hour_frac = w_start.hour + w_start.minute / 60.0

            base_score  = _congestion_score_at(hour_frac, cam_idx)
            # Small Gaussian jitter — std dev = 0.025 so there's texture without spikes
            noise       = random.gauss(0, 0.025)
            score       = round(min(1.0, max(0.0, base_score + noise)), 4)

            # Speed inversely proportional to congestion (score 0-1)
            speed = round(base_speed * (1.0 - score * 0.75) + random.gauss(0, 1.0), 1)
            speed = max(3.0, speed)

            # Vehicle count scales with congestion (realistic 0-80 vehicles per 5-min window)
            vehicle_count = int(5 + score * 70 + random.gauss(0, 2))
            vehicle_count = max(0, vehicle_count)

            queue_length  = int(max(0, (score - 0.4) * 40 + random.gauss(0, 1)))
            stopped_ratio = round(max(0.0, min(1.0, max(0.0, score - 0.5) * 0.8 + random.gauss(0, 0.02))), 3)

            records.append(TrafficMetric(
                camera_id=cam_id,
                window_start=w_start,
                window_end=w_end,
                lane_id=None,
                vehicle_count=vehicle_count,
                counts_by_class=None,
                avg_speed_kmh=speed,
                stopped_ratio=stopped_ratio,
                queue_length=queue_length,
                congestion_level=_congestion_level(score),
                congestion_score=score,
            ))

    db.bulk_save_objects(records)
    db.commit()
    logger.info("mock_traffic: seeded %d traffic_metric rows across %d cameras", len(records), len(cameras))


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
