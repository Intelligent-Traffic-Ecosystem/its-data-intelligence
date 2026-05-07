from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TrafficEvent(Base):
    __tablename__ = "traffic_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vehicle_id: Mapped[str | None] = mapped_column(Text)
    vehicle_class: Mapped[str | None] = mapped_column("class", Text)
    centroid_x: Mapped[float | None] = mapped_column(Float)
    centroid_y: Mapped[float | None] = mapped_column(Float)
    speed_kmh: Mapped[float | None] = mapped_column(Float)
    frame_id: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[float | None] = mapped_column(Float)
    bbox_x: Mapped[int | None] = mapped_column(Integer)
    bbox_y: Mapped[int | None] = mapped_column(Integer)
    bbox_w: Mapped[int | None] = mapped_column(Integer)
    bbox_h: Mapped[int | None] = mapped_column(Integer)
    lane_id: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_traffic_events_camera_ts", "camera_id", ts.desc()),
        Index("ix_traffic_events_camera_lane_ts", "camera_id", "lane_id", ts.desc()),
    )


class TrafficMetric(Base):
    __tablename__ = "traffic_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lane_id: Mapped[int | None] = mapped_column(Integer)
    vehicle_count: Mapped[int | None] = mapped_column(Integer)
    counts_by_class: Mapped[str | None] = mapped_column(Text)  # JSON string
    avg_speed_kmh: Mapped[float | None] = mapped_column(Float)
    stopped_ratio: Mapped[float | None] = mapped_column(Float)
    queue_length: Mapped[int | None] = mapped_column(Integer)
    congestion_level: Mapped[str | None] = mapped_column(Text)
    congestion_score: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint(
            "camera_id", "lane_id", "window_start", name="uq_metrics_camera_lane_window"
        ),
        Index("ix_traffic_metrics_camera_window", "camera_id", window_start.desc()),
    )


class AdminThreshold(Base):
    __tablename__ = "admin_thresholds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    congestion_threshold_low: Mapped[float] = mapped_column(Float, nullable=False)
    congestion_threshold_moderate: Mapped[float] = mapped_column(Float, nullable=False)
    congestion_threshold_high: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MonitoringZone(Base):
    __tablename__ = "monitoring_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    polygon_wgs84: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AlertAcknowledgement(Base):
    __tablename__ = "alert_acknowledgements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    admin_id: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_alert_acknowledgements_alert_id", "alert_id"),)
