"""
Kafka-driven real-time prediction loop.

:class:`PredictionStreamConsumer` listens on the ``traffic.metrics`` Kafka
topic (published by the upstream stream-processor after each 5-second window
flush) and triggers the ST-GCN predictor on every incoming message.

Prediction results are serialised to JSON and published back to the
``traffic.predictions`` topic so downstream consumers (dashboards, alert
services, the FastAPI WebSocket endpoint) can receive them without polling
the prediction API.

The consumer intentionally uses **manual offset commits** (``enable_auto_commit=False``)
so that a crash during inference does not lose the triggering message.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime, timezone
from typing import Any

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError

from src.config import Config, get_config
from src.inference.predictor import LaneForecast, TrafficPredictor

logger = logging.getLogger(__name__)

_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _forecast_to_dict(fc: LaneForecast) -> dict[str, Any]:
    """Serialise a :class:`~src.inference.predictor.LaneForecast` to a JSON-safe dict."""

    def _dt(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    return {
        "camera_id": fc.camera_id,
        "lane_id": fc.lane_id,
        "generated_at": _dt(fc.generated_at),
        "forecast_horizon_steps": len(fc.predicted_vehicle_counts),
        "congestion_start": _dt(fc.congestion_start),
        "congestion_end": _dt(fc.congestion_end),
        "peak_congestion_probability": round(fc.peak_congestion_probability, 4),
        # Downsample to a manageable number of summary points (every 12 steps = 1 min)
        "summary_counts": _downsample(fc.predicted_vehicle_counts, step=12),
        "summary_probabilities": _downsample(fc.congestion_probabilities, step=12),
        "summary_levels": _downsample(fc.congestion_levels, step=12),
        "summary_timestamps": [
            _dt(ts) for ts in _downsample(fc.forecast_timestamps, step=12)
        ],
    }


def _downsample(seq: list, step: int) -> list:
    return seq[::step] if seq else []


class PredictionStreamConsumer:
    """
    Kafka consumer that triggers predictions on each incoming metrics message.

    Parameters
    ----------
    config:
        App config. Defaults to global singleton.
    predictor:
        Optional pre-built :class:`~src.inference.predictor.TrafficPredictor`.
        If ``None`` it is constructed lazily on first message.
    """

    def __init__(
        self,
        config: Config | None = None,
        predictor: TrafficPredictor | None = None,
    ) -> None:
        self._cfg = config or get_config()
        kcfg = self._cfg.kafka
        self._predictor = predictor
        self._running = False

        self._consumer = KafkaConsumer(
            kcfg.topic_metrics,
            bootstrap_servers=kcfg.brokers.split(","),
            group_id=kcfg.consumer_group,
            auto_offset_reset=kcfg.auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            consumer_timeout_ms=5_000,
        )

        self._producer = KafkaProducer(
            bootstrap_servers=kcfg.brokers.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=3,
        )

        self._topic_out = kcfg.topic_predictions
        self._last_prediction_ts: float = 0.0
        self._min_interval_sec = self._cfg.inference.poll_interval_seconds

        logger.info(
            "PredictionStreamConsumer ready — input=%s  output=%s  group=%s",
            kcfg.topic_metrics,
            kcfg.topic_predictions,
            kcfg.consumer_group,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the blocking consumer loop.

        Registers SIGTERM/SIGINT handlers for graceful shutdown.
        """
        self._running = True
        for sig in _SHUTDOWN_SIGNALS:
            signal.signal(sig, self._handle_signal)

        logger.info("Consumer loop started.")
        try:
            while self._running:
                self._poll_and_predict()
        finally:
            self._shutdown()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _poll_and_predict(self) -> None:
        """Poll Kafka for new messages; trigger prediction if throttle allows."""
        try:
            for _msg in self._consumer:
                # Throttle: do not re-run inference more often than poll_interval_seconds
                now = time.monotonic()
                if now - self._last_prediction_ts < self._min_interval_sec:
                    self._consumer.commit()
                    continue

                self._run_prediction()
                self._last_prediction_ts = time.monotonic()
                self._consumer.commit()

                if not self._running:
                    break
        except KafkaError as exc:
            logger.error("Kafka error: %s — retrying in 5 s", exc)
            time.sleep(5)

    def _run_prediction(self) -> None:
        """Initialise predictor if needed, run inference, publish results."""
        if self._predictor is None:
            logger.info("Initialising TrafficPredictor...")
            self._predictor = TrafficPredictor(self._cfg)

        try:
            forecasts = self._predictor.predict()
        except Exception as exc:
            logger.exception("Prediction failed: %s", exc)
            return

        if not forecasts:
            return

        payload = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "num_lanes": len(forecasts),
            "forecasts": [_forecast_to_dict(fc) for fc in forecasts],
        }

        try:
            self._producer.send(self._topic_out, value=payload)
            self._producer.flush(timeout=5)
            logger.info(
                "Published predictions for %d lanes → %s",
                len(forecasts), self._topic_out,
            )
        except KafkaError as exc:
            logger.error("Failed to publish predictions: %s", exc)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("Shutdown signal received (%d).", signum)
        self._running = False

    def _shutdown(self) -> None:
        logger.info("Closing Kafka connections...")
        try:
            self._consumer.close()
            self._producer.close()
        except Exception as exc:
            logger.warning("Error during shutdown: %s", exc)
        logger.info("Consumer stopped.")
