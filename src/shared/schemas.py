from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

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


# --- Admin controls ---


class Thresholds(BaseModel):
    congestion_threshold_low: float = Field(ge=0, le=1)
    congestion_threshold_moderate: float = Field(ge=0, le=1)
    congestion_threshold_high: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self):
        if not (
            self.congestion_threshold_low
            <= self.congestion_threshold_moderate
            <= self.congestion_threshold_high
        ):
            raise ValueError("Thresholds must satisfy low <= moderate <= high")
        return self


class WGS84Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


def _validate_polygon(points: list[WGS84Point]) -> list[WGS84Point]:
    if len(points) < 3:
        raise ValueError("Polygon must contain at least 3 points")

    unique = {(p.lat, p.lon) for p in points}
    if len(unique) < 3:
        raise ValueError("Polygon must contain at least 3 distinct points")

    return points


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    coordinates: list[WGS84Point]

    _validate_coordinates = field_validator("coordinates")(_validate_polygon)


class ZoneUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    coordinates: list[WGS84Point]

    _validate_coordinates = field_validator("coordinates")(_validate_polygon)


class ZoneOut(BaseModel):
    id: int
    name: str
    description: str | None
    coordinates: list[dict]
    created_at: datetime
    updated_at: datetime


class BroadcastNotification(BaseModel):
    severity: str = Field(pattern="^(info|warning|critical)$")
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)


# --- Alerts ---


class AlertOutput(BaseModel):
    id: int
    camera_id: str
    road_segment: str | None = None
    alert_type: str
    severity: str
    title: str
    message: str
    congestion_level: str | None = None
    congestion_score: float | None = None
    triggered_at: datetime
    resolved_at: datetime | None = None
    acknowledged: bool
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None


class AlertAcknowledgeRequest(BaseModel):
    admin_id: str = Field(min_length=1, max_length=200)


class AlertAcknowledgeResponse(BaseModel):
    alert_id: int
    admin_id: str
    acknowledged_at: datetime
    status: str


class CameraRegistryCreate(BaseModel):
    model_config = {"populate_by_name": True}

    camera_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(
        ge=-90, le=90, validation_alias=AliasChoices("latitude", "lat")
    )
    longitude: float = Field(
        ge=-180, le=180, validation_alias=AliasChoices("longitude", "lng")
    )
    road_segment: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)


class CameraRegistryUpdate(BaseModel):
    model_config = {"populate_by_name": True}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    latitude: float | None = Field(
        default=None, ge=-90, le=90, validation_alias=AliasChoices("latitude", "lat")
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        validation_alias=AliasChoices("longitude", "lng"),
    )
    road_segment: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)


class CameraRegistryOut(BaseModel):
    id: int
    camera_id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    road_segment: str | None = None
    description: str | None = None
    created_at: datetime
    updated_at: datetime


# --- Analytics ---


class PeakHourTrend(BaseModel):
    hour: int = Field(ge=0, le=23)
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


class AnalyticsRangeSummary(BaseModel):
    start: datetime
    end: datetime
    average_congestion: float
    vehicle_count: int
    average_speed_kmh: float
    incident_count: int
    peak_hour: int | None = Field(default=None, ge=0, le=23)


class AnalyticsMetricsResponse(BaseModel):
    start: datetime
    end: datetime
    average_congestion: float
    peak_hour_trends: list[PeakHourTrend]
    top_segments: list[TopSegment]
    incidents: IncidentSummary


class AnalyticsCompareResponse(BaseModel):
    range_a: AnalyticsRangeSummary
    range_b: AnalyticsRangeSummary
    congestion_delta: float
    vehicle_count_delta: int
    speed_delta: float
    incident_delta: int
