"""Lazy ST-GCN bridge for the main B2 API (wraps ``traffic-predictor``)."""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.config import Settings, settings
from shared.models import TrafficMetric

logger = logging.getLogger(__name__)

_predictor = None
_predictor_lock = threading.Lock()


class StgcnInferenceError(Exception):
    """Raised when ST-GCN cannot produce a forecast (configuration or runtime)."""

    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def _traffic_predictor_home() -> Path:
    """Resolve the ``traffic-predictor`` package directory (contains ``src/``)."""
    configured = (settings.traffic_predictor_home or "").strip()
    if configured:
        p = Path(configured).expanduser().resolve()
        if (p / "src" / "config.py").is_file():
            return p
        raise StgcnInferenceError(
            f"TRAFFIC_PREDICTOR_HOME does not look like traffic-predictor: {p}",
            status_code=503,
        )

    cur = Path(__file__).resolve().parent
    for _ in range(8):
        cand = cur / "traffic-predictor"
        if (cand / "src" / "config.py").is_file():
            return cand.resolve()
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    raise StgcnInferenceError(
        "Cannot find traffic-predictor/ next to the source tree or working directory. "
        "Set TRAFFIC_PREDICTOR_HOME to that directory.",
        status_code=503,
    )


def _ensure_predictor_on_path(home: Path) -> None:
    root = str(home.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def _config_file(home: Path) -> Path:
    raw = (settings.stgcn_config_path or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p.resolve() if p.is_absolute() else (home / p).resolve()
    return (home / "config.yaml").resolve()


def _resolve_checkpoint(home: Path, cfg_path: Path, ckpt: str) -> Path:
    p = Path(ckpt).expanduser()
    if p.is_absolute():
        return p.resolve()
    base = cfg_path.parent if cfg_path.is_file() else home
    return (base / p).resolve()


def get_predictor(app_settings: Settings | None = None) -> object:
    """Return a singleton :class:`TrafficPredictor` (imports on first use)."""
    global _predictor
    cfg_settings = app_settings or settings
    if _predictor is None:
        with _predictor_lock:
            if _predictor is None:
                home = _traffic_predictor_home()
                _ensure_predictor_on_path(home)
                from src.config import load_config  # noqa: PLC0415 — after sys.path
                from src.inference.predictor import TrafficPredictor  # noqa: PLC0415

                cfg_path = _config_file(home)
                if not cfg_path.is_file():
                    raise StgcnInferenceError(
                        f"ST-GCN config file not found: {cfg_path}", status_code=503
                    )

                cfg = load_config(str(cfg_path))
                cfg = replace(cfg, database=replace(cfg.database, url=cfg_settings.postgres_url))
                ck = (cfg_settings.stgcn_checkpoint_path or "").strip() or cfg.inference.checkpoint_path
                ck_resolved = str(_resolve_checkpoint(home, cfg_path, ck))
                cfg = replace(cfg, inference=replace(cfg.inference, checkpoint_path=ck_resolved))
                if not Path(ck_resolved).is_file():
                    raise StgcnInferenceError(
                        f"ST-GCN checkpoint not found: {ck_resolved}. "
                        "Train a model or set STGCN_CHECKPOINT_PATH.",
                        status_code=503,
                    )
                _predictor = TrafficPredictor(config=cfg)
                logger.info("stgcn_predictor_loaded checkpoint=%s", ck_resolved)
    return _predictor


def reset_predictor_for_tests() -> None:
    """Drop the singleton (tests only)."""
    global _predictor
    _predictor = None


def _level_from_probability(score: float, s: Settings) -> str:
    low, moderate, high = (
        s.congestion_threshold_low,
        s.congestion_threshold_moderate,
        s.congestion_threshold_high,
    )
    if score < low:
        return "LOW"
    if score < moderate:
        return "MODERATE"
    if score < high:
        return "HIGH"
    return "SEVERE"


def forecast_camera_stgcn(
    db: Session,
    app_settings: Settings,
    *,
    camera_id: str,
    lookback_minutes: int,
    horizon_minutes: int,
) -> dict:
    predictor = get_predictor(app_settings)
    step_seconds = max(1, predictor._cfg.features.window_size_seconds)
    horizon_steps = max(1, (horizon_minutes * 60) // step_seconds)

    forecasts = predictor.predict(lookback_minutes=lookback_minutes)
    if not forecasts:
        raise StgcnInferenceError(
            "No recent traffic metrics available for ST-GCN inference.",
            status_code=404,
        )
    lanes = [f for f in forecasts if f.camera_id == camera_id]
    if not lanes:
        raise StgcnInferenceError(
            f"Camera {camera_id!r} is not in the ST-GCN topology or inference returned no rows.",
            status_code=404,
        )

    t_max = min(len(lanes[0].congestion_probabilities), horizon_steps)
    for f in lanes[1:]:
        t_max = min(t_max, len(f.congestion_probabilities))
    if t_max <= 0:
        raise StgcnInferenceError(
            f"No forecast steps available for camera {camera_id!r}.",
            status_code=404,
        )

    forecast_payload: list[dict] = []
    for t in range(t_max):
        p = sum(f.congestion_probabilities[t] for f in lanes) / len(lanes)
        p = min(max(float(p), 0.0), 1.0)
        ts = lanes[0].forecast_timestamps[t]
        forecast_payload.append(
            {
                "step": t + 1,
                "predicted_at": ts.isoformat(),
                "congestion_score": round(p, 4),
                "congestion_level": _level_from_probability(p, app_settings),
            }
        )

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    if db.get_bind().dialect.name == "sqlite":
        cutoff = cutoff.replace(tzinfo=None)
    history_stmt = (
        select(func.count())
        .select_from(TrafficMetric)
        .where(
            TrafficMetric.camera_id == camera_id,
            TrafficMetric.lane_id.is_not(None),
            TrafficMetric.window_start >= cutoff,
        )
    )
    history_samples = int(db.execute(history_stmt).scalar() or 0)

    return {
        "camera_id": camera_id,
        "model": "stgcn",
        "horizon_minutes": horizon_minutes,
        "step_seconds": step_seconds,
        "history_samples": history_samples,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast": forecast_payload,
    }
