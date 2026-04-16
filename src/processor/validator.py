import json
import logging

from shared.schemas import TrafficEventInput

logger = logging.getLogger(__name__)

VALID_CLASSES = {"car", "bus", "truck", "motorcycle", "bicycle", "pedestrian"}


def validate_event(raw: str | bytes) -> TrafficEventInput | None:
    """Validate a raw JSON event from Kafka. Returns parsed event or None if invalid."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON: %s", raw[:200] if raw else "<empty>")
        return None

    try:
        event = TrafficEventInput.model_validate(data)
    except Exception as e:
        logger.warning("Schema validation failed: %s — event: %s", e, data.get("vehicle_id", "?"))
        return None

    if event.vehicle_class not in VALID_CLASSES:
        logger.warning("Unknown vehicle class '%s' from camera %s", event.vehicle_class, event.camera_id)
        return None

    return event
