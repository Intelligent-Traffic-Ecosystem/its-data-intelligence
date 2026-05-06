"""
Centralised configuration for the ST-GCN traffic-forecasting module.

Config is loaded from ``config.yaml`` (default) and can be overridden via
environment variables prefixed with ``STGCN_``.

Usage::

    from src.config import get_config
    cfg = get_config()
    print(cfg.database.url)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Topology sub-configs
# ---------------------------------------------------------------------------


@dataclass
class CameraTopology:
    camera_id: str
    lane_ids: list[int]


@dataclass
class CrossCameraEdge:
    from_camera: str
    from_lane: int
    to_camera: str
    to_lane: int


@dataclass
class TopologyConfig:
    cameras: list[CameraTopology]
    cross_camera_edges: list[CrossCameraEdge] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Feature config
# ---------------------------------------------------------------------------


@dataclass
class NormalizationConfig:
    vehicle_count_max: int = 50
    speed_max_kmh: float = 60.0


@dataclass
class FeatureConfig:
    node_features: list[str] = field(default_factory=list)
    input_window: int = 12
    output_horizon: int = 360
    window_size_seconds: int = 5
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)

    @property
    def num_node_features(self) -> int:
        return len(self.node_features)


# ---------------------------------------------------------------------------
# Model config
# ---------------------------------------------------------------------------


@dataclass
class LossWeightsConfig:
    vehicle_count: float = 1.0
    congestion_prob: float = 0.5
    congestion_level: float = 0.5


@dataclass
class HeadsConfig:
    vehicle_count: bool = True
    congestion_prob: bool = True
    congestion_level: bool = True


@dataclass
class ModelConfig:
    hidden_channels: int = 64
    num_stgcn_blocks: int = 3
    temporal_kernel_size: int = 3
    cheb_k: int = 3
    dropout: float = 0.1
    heads: HeadsConfig = field(default_factory=HeadsConfig)
    loss_weights: LossWeightsConfig = field(default_factory=LossWeightsConfig)


# ---------------------------------------------------------------------------
# Training config
# ---------------------------------------------------------------------------


@dataclass
class LRSchedulerConfig:
    type: str = "cosine"
    T_max: int = 100
    eta_min: float = 1e-5


@dataclass
class TrainingConfig:
    epochs: int = 100
    batch_size: int = 32
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    lr_scheduler: LRSchedulerConfig = field(default_factory=LRSchedulerConfig)
    early_stopping_patience: int = 10
    val_fraction: float = 0.15
    test_fraction: float = 0.10
    checkpoint_dir: str = "checkpoints"
    log_interval: int = 10


# ---------------------------------------------------------------------------
# Inference config
# ---------------------------------------------------------------------------


@dataclass
class CongestionEstimatorConfig:
    start_threshold: float = 0.55
    end_threshold: float = 0.30
    min_duration_steps: int = 3


@dataclass
class InferenceConfig:
    checkpoint_path: str = "checkpoints/best_model.pt"
    device: str = "cpu"
    poll_interval_seconds: int = 5
    db_lookback_minutes: int = 10
    congestion: CongestionEstimatorConfig = field(
        default_factory=CongestionEstimatorConfig
    )


# ---------------------------------------------------------------------------
# Database / Kafka / API / Logging configs
# ---------------------------------------------------------------------------


@dataclass
class DatabaseConfig:
    url: str = "postgresql://user:pass@localhost:5432/traffic"
    pool_size: int = 5
    max_overflow: int = 10


@dataclass
class KafkaConfig:
    brokers: str = "kafka:9092"
    topic_metrics: str = "traffic.metrics"
    topic_predictions: str = "traffic.predictions"
    consumer_group: str = "stgcn-predictor"
    auto_offset_reset: str = "latest"


@dataclass
class APIConfig:
    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "info"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


@dataclass
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    kafka: KafkaConfig = field(default_factory=KafkaConfig)
    topology: TopologyConfig = field(
        default_factory=lambda: TopologyConfig(cameras=[])
    )
    features: FeatureConfig = field(default_factory=FeatureConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    api: APIConfig = field(default_factory=APIConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Recursively construct dataclass instances from a nested dict."""
    if not data:
        return cls()
    kwargs: dict[str, Any] = {}
    for f in cls.__dataclass_fields__.values():  # type: ignore[union-attr]
        val = data.get(f.name)
        if val is None:
            continue
        ftype = f.type
        # Resolve forward references / string annotations
        if isinstance(ftype, str):
            import sys

            ftype = eval(ftype, sys.modules[cls.__module__].__dict__)  # noqa: S307
        origin = getattr(ftype, "__origin__", None)
        if origin is list and val:
            inner = ftype.__args__[0]
            if hasattr(inner, "__dataclass_fields__"):
                val = [_from_dict(inner, item) for item in val]
        elif hasattr(ftype, "__dataclass_fields__") and isinstance(val, dict):
            val = _from_dict(ftype, val)
        kwargs[f.name] = val
    return cls(**kwargs)


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from *path* (defaults to ``config.yaml`` next to cwd)."""
    if path is None:
        # Walk up from current file until we find config.yaml
        search = Path(__file__).parent
        for _ in range(5):
            candidate = search / "config.yaml"
            if candidate.exists():
                path = candidate
                break
            search = search.parent
        if path is None:
            return Config()

    with open(path, "r") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    # Allow env-var overrides: STGCN_DATABASE__URL=... overrides database.url
    _apply_env_overrides(raw)

    return _from_dict(Config, raw)


def _apply_env_overrides(raw: dict[str, Any]) -> None:
    """Merge ``STGCN_<SECTION>__<KEY>`` environment variables into *raw*."""
    prefix = "STGCN_"
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        node = raw
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = val


@lru_cache(maxsize=1)
def get_config(path: str | None = None) -> Config:
    """Return the singleton :class:`Config` instance (cached after first call)."""
    return load_config(path)
