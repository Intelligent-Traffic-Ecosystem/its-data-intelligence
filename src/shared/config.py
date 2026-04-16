from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Kafka
    kafka_brokers: str = "kafka:9092"
    kafka_topic_input: str = "traffic.events.raw"

    # PostgreSQL
    postgres_url: str = "postgresql://user:pass@postgres:5432/traffic"

    # Stream Processor
    window_size_seconds: int = 5

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
