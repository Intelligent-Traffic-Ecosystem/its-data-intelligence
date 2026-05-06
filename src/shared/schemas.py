from datetime import datetime

from pydantic import BaseModel, Field, model_validator

# --- Input event from B1 via Kafka ---

class BBox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Centroid(BaseModel):
    x: int
    y: int


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


class Thresholds(BaseModel):
    congestion_threshold_low: float = Field(ge=0, le=1)
    congestion_threshold_moderate: float = Field(ge=0, le=1)
    congestion_threshold_high: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _validate_order(self) -> "Thresholds":
        if not (
            self.congestion_threshold_low
            < self.congestion_threshold_moderate
            < self.congestion_threshold_high
        ):
            raise ValueError("thresholds must be increasing (low < moderate < high)")
        return self


class Wgs84Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ZoneBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    coordinates: list[Wgs84Point] = Field(min_length=3)


class ZoneCreate(ZoneBase):
    pass


class ZoneUpdate(ZoneBase):
    pass


class ZoneOut(ZoneBase):
    id: int
    created_at: datetime
    updated_at: datetime


class BroadcastNotification(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    severity: str = Field(default="info", pattern="^(info|warning|critical)$")
    title: str | None = Field(default=None, max_length=200)


# --- Cameras (admin-managed registry with geo + road segment) ---


class CameraBase(BaseModel):
    camera_id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    road_segment: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    road_segment: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class CameraOut(CameraBase):
    id: int
    created_at: datetime
    updated_at: datetime


# --- Alerts ---

ALERT_SEVERITIES = ("WARNING", "CRITICAL", "EMERGENCY")
ALERT_TYPES = ("congestion", "stopped_traffic", "incident", "manual")


class AlertOut(BaseModel):
    id: int
    severity: str
    alert_type: str
    camera_id: str | None
    road_segment: str | None
    title: str
    message: str | None
    congestion_level: str | None
    congestion_score: float | None
    triggered_at: datetime
    resolved_at: datetime | None
    acknowledged_by: str | None
    acknowledged_at: datetime | None


class AlertAcknowledgeResponse(BaseModel):
    id: int
    acknowledged_by: str
    acknowledged_at: datetime


# --- Dashboard ---


class DashboardSummary(BaseModel):
    total_incidents_24h: int
    avg_speed_kmh: float
    overall_congestion_level: str
    overall_congestion_score: float
    active_alerts: int
    last_updated: datetime


class DashboardEvent(BaseModel):
    camera_id: str
    timestamp: datetime
    vehicle_class: str | None
    speed_kmh: float | None
    lane_id: int | None


# --- Map ---


class HeatmapPoint(BaseModel):
    camera_id: str
    latitude: float
    longitude: float
    weight: float
    vehicle_count: int


class IncidentMarker(BaseModel):
    alert_id: int
    camera_id: str | None
    latitude: float | None
    longitude: float | None
    severity: str
    alert_type: str
    title: str
    triggered_at: datetime


# --- Analytics ---


class PeakHourBucket(BaseModel):
    hour: int
    avg_vehicle_count: float
    avg_congestion_score: float


class TopSegment(BaseModel):
    camera_id: str
    road_segment: str | None
    avg_congestion_score: float
    severe_minutes: float


class IncidentSlice(BaseModel):
    severity: str
    count: int


class AnalyticsMetrics(BaseModel):
    range_start: datetime
    range_end: datetime
    avg_congestion_score: float
    peak_hour_distribution: list[PeakHourBucket]
    top_segments: list[TopSegment]
    incident_pie: list[IncidentSlice]


class AnalyticsCompare(BaseModel):
    range_a: AnalyticsMetrics
    range_b: AnalyticsMetrics
