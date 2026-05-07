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
    alert_id: str
    camera_id: str
    road_segment: str | None = None
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
    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None


class AlertAcknowledgeRequest(BaseModel):
    admin_id: str


class AlertAcknowledgeResponse(BaseModel):
    alert_id: str
    admin_id: str
    acknowledged_at: datetime
    status: str


class DashboardSummaryResponse(BaseModel):
    active_cameras: int
    total_vehicles: int
    average_speed: float
    severe_congestion_count: int
    busiest_camera_id: str | None = None


class TrafficEventOutput(BaseModel):
    id: int
    camera_id: str
    timestamp: datetime
    vehicle_id: str | None = None
    vehicle_class: str | None = None
    speed_kmh: float | None = None
    confidence: float | None = None
    lane_id: int | None = None
    bbox_x: int | None = None
    bbox_y: int | None = None
    bbox_w: int | None = None
    bbox_h: int | None = None


class HeatmapDataOutput(BaseModel):
    camera_id: str
    latitude: float | None = None
    longitude: float | None = None
    vehicle_count: int
    congestion_score: float
    congestion_level: str


class IncidentOutput(BaseModel):
    incident_id: str
    camera_id: str
    latitude: float | None = None
    longitude: float | None = None
    severity: str
    description: str
    timestamp: datetime
