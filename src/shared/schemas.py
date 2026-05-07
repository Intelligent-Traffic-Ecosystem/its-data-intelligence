from datetime import datetime

from pydantic import BaseModel, Field

# --- Input event from B1 via Kafka ---


class BBox(BaseModel):
    # B1 rounds pixel coords to 2 decimals; accept floats and cast in the writer.
    x: float
    y: float
    w: float
    h: float


class Centroid(BaseModel):
    x: float
    y: float


class TrafficEventInput(BaseModel):
    camera_id: str
    timestamp: datetime
    frame_id: int
    vehicle_id: str
    vehicle_class: str = Field(alias="class")
    confidence: float = Field(ge=0, le=1)
    bbox: BBox
    centroid: Centroid
    lane_id: int | None = None
    speed_estimate: float | None = None

    model_config = {"populate_by_name": True}


# --- Output metric to B3 via REST / WebSocket ---


class TrafficMetricOutput(BaseModel):
    camera_id: str
    window_start: datetime
    window_end: datetime
    lane_id: int | None = None
    vehicle_count: int
    counts_by_class: dict[str, int]
    avg_speed_kmh: float
    stopped_ratio: float
    queue_length: int
    congestion_level: str
    congestion_score: float


class CameraInfo(BaseModel):
    camera_id: str
    last_seen: datetime | None = None


class HealthResponse(BaseModel):
    status: str
    kafka: str
    postgres: str

class AlertOutput(BaseModel):
    camera_id: str
    lane_id: int | None = None
    alert_type: str
    severity: str
    message: str
    window_start: datetime
    window_end: datetime
    congestion_level: str
    congestion_score: float
    vehicle_count: int
    avg_speed_kmh: float
    queue_length: int