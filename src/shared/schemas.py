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
