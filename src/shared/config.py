import json

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Kafka
    kafka_brokers: str = "kafka:9092"
    kafka_topic_input: str = "traffic.events.raw"

    # PostgreSQL
    postgres_url: str = "postgresql://user:pass@postgres:5432/traffic"

    # Stream Processor
    window_size_seconds: int = 5
    window_allowed_lateness_seconds: int = 1
    flink_parallelism: int = 1
    flink_checkpoint_interval_ms: int = 30000

    # Congestion weights
    congestion_weight_count: float = 0.4
    congestion_weight_speed: float = 0.4
    congestion_weight_stopped: float = 0.2

    # Congestion thresholds
    congestion_threshold_low: float = 0.30
    congestion_threshold_moderate: float = 0.55
    congestion_threshold_high: float = 0.80

    # Normalisation maximums
    max_vehicle_count: int = 50
    max_speed_kmh: float = 60.0

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    ws_broadcast_interval: int = 5

    # Speed fallback (centroid-based)
    speed_tracker_ttl_seconds: int = 30
    speed_tracker_pixel_to_meter: float = 0.05
    camera_calibrations: dict[str, float] = Field(default_factory=dict)

    @field_validator("camera_calibrations", mode="before")
    @classmethod
    def parse_camera_calibrations(cls, value):
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            return json.loads(value)
        return value

    # Raw event batched writer
    raw_writer_batch_size: int = 200
    raw_writer_flush_interval_seconds: float = 1.0

    # Retention sweep
    retention_events_hours: int = 24
    retention_metrics_days: int = 30
    retention_sweep_interval_seconds: int = 3600
    metrics_archive_enabled: bool = False
    metrics_archive_path: str = "/tmp/its-data-intelligence/traffic_metrics"

    # Processor Prometheus port
    processor_metrics_port: int = 9100

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
