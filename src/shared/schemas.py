from datetime import datetime

from pydantic import BaseModel, Field, model_validator

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
<<<<<<< HEAD


class PeakHourTrend(BaseModel):
    hour: int
    average_congestion: float
    average_speed_kmh: float
    vehicle_count: int


class TopSegment(BaseModel):
    camera_id: str
    lane_id: int | None = None
    average_congestion: float
    average_queue_length: float
    incident_count: int


class IncidentSummary(BaseModel):
    total_incidents: int
    high: int
    critical: int


class AnalyticsMetricsResponse(BaseModel):
    start: datetime
    end: datetime
    average_congestion: float
    peak_hour_trends: list[PeakHourTrend]
    top_segments: list[TopSegment]
    incidents: IncidentSummary


class AnalyticsRangeSummary(BaseModel):
    start: datetime
    end: datetime
    average_congestion: float
    vehicle_count: int
    average_speed_kmh: float
    incident_count: int
    peak_hour: int | None = None


class AnalyticsCompareResponse(BaseModel):
    range_a: AnalyticsRangeSummary
    range_b: AnalyticsRangeSummary
    congestion_delta: float
    vehicle_count_delta: int
    speed_delta: float
    incident_delta: int
