import logging
from datetime import datetime, timezone
from typing import Iterable

from pyflink.common import Duration
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.watermark_strategy import TimestampAssigner, WatermarkStrategy
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import FlinkKafkaConsumer
from pyflink.datastream.functions import FlatMapFunction, MapFunction, ProcessWindowFunction
from pyflink.datastream.window import Time, TumblingEventTimeWindows

from processor.metrics_prom import EVENTS_DROPPED, EVENTS_PROCESSED, WINDOWS_FLUSHED
from processor.speed_tracker import SpeedTracker
from processor.validator import validate_event
from processor.writer import BatchedRawWriter, write_metrics
from shared.config import settings
from shared.schemas import TrafficEventInput

logger = logging.getLogger(__name__)


class ValidateAndTrackSpeed(FlatMapFunction):
    def __init__(self):
        self.tracker = None

    def open(self, runtime_context):
        self.tracker = SpeedTracker()

    def flat_map(self, value: str):
        event = validate_event(value)
        if event is None:
            EVENTS_DROPPED.labels(reason="validation").inc()
            return

        if event.speed_estimate is None:
            est = self.tracker.estimate(event)
            if est is not None:
                event.speed_estimate = est
        else:
            self.tracker.estimate(event)

        self.tracker.evict_stale()
        EVENTS_PROCESSED.inc()

        # PyFlink implicitly pickles Python objects when output_type is not specified.
        # However, to be safe with watermarks and keys, we emit a dict.
        yield event.model_dump()


class EventTimestampAssigner(TimestampAssigner):
    def extract_timestamp(self, value, record_timestamp):
        ts = value.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts is None:
            return record_timestamp
        return int(ts.timestamp() * 1000)


class AggregateMetricsFunction(ProcessWindowFunction):
    def process(
        self, key, context: ProcessWindowFunction.Context, elements: Iterable[dict]
    ) -> Iterable[dict]:
        from processor.congestion import classify_congestion
        from processor.metrics import compute_metrics

        events = [TrafficEventInput.model_validate(e) for e in elements]
        if not events:
            return

        m = compute_metrics(events)
        level, score = classify_congestion(
            m["vehicle_count"], m["avg_speed_kmh"], m["stopped_ratio"]
        )

        window = context.window()
        window_start = datetime.fromtimestamp(window.start / 1000.0, tz=timezone.utc)
        window_end = datetime.fromtimestamp(window.end / 1000.0, tz=timezone.utc)

        camera_id = key[0] if isinstance(key, tuple) else key

        result_global = {
            "camera_id": camera_id,
            "lane_id": None,
            "window_start": window_start,
            "window_end": window_end,
            **m,
            "congestion_level": level,
            "congestion_score": score,
        }
        yield result_global

        # Per-lane aggregation
        lanes = {e.lane_id for e in events if e.lane_id is not None}
        for lane_id in lanes:
            lane_events = [e for e in events if e.lane_id == lane_id]
            lm = compute_metrics(lane_events)
            ll, ls = classify_congestion(
                lm["vehicle_count"], lm["avg_speed_kmh"], lm["stopped_ratio"]
            )

            yield {
                "camera_id": camera_id,
                "lane_id": lane_id,
                "window_start": window_start,
                "window_end": window_end,
                **lm,
                "congestion_level": ll,
                "congestion_score": ls,
            }


class RawEventSink(MapFunction):
    def __init__(self):
        self.writer = None

    def open(self, runtime_context):
        self.writer = BatchedRawWriter()

    def map(self, value: dict):
        event = TrafficEventInput.model_validate(value)
        self.writer.add(event)
        self.writer.flush_due()
        return value

    def close(self):
        if self.writer:
            self.writer.flush()


class MetricSink(MapFunction):
    def map(self, value: dict):
        write_metrics([value])
        WINDOWS_FLUSHED.inc()
        return value


def build_pipeline(env: StreamExecutionEnvironment):
    kafka_props = {
        "bootstrap.servers": settings.kafka_brokers,
        "group.id": "b2-stream-processor-flink",
        "auto.offset.reset": "latest",
    }

    kafka_consumer = FlinkKafkaConsumer(
        topics=settings.kafka_topic_input,
        deserialization_schema=SimpleStringSchema(),
        properties=kafka_props,
    )

    # 1. Source
    stream = env.add_source(kafka_consumer).name("kafka_source")

    # 2. Validation & Speed Tracking
    events = stream.flat_map(ValidateAndTrackSpeed()).name("validate_and_track")

    # 3. Raw Events Sink (Branch 1)
    events.map(RawEventSink()).name("raw_events_sink")

    # 4. Watermarks & Windowing (Branch 2)
    watermark_strategy = WatermarkStrategy.for_bounded_out_of_orderness(
        Duration.of_seconds(settings.window_allowed_lateness_seconds)
    ).with_timestamp_assigner(EventTimestampAssigner())

    windowed_metrics = (
        events.assign_timestamps_and_watermarks(watermark_strategy)
        .key_by(lambda e: e["camera_id"])
        .window(TumblingEventTimeWindows.of(Time.seconds(settings.window_size_seconds)))
        .allowed_lateness(settings.window_allowed_lateness_seconds * 1000)
        .process(AggregateMetricsFunction())
        .name("window_aggregation")
    )

    # 5. Metrics Sink
    windowed_metrics.map(MetricSink()).name("metrics_sink")
