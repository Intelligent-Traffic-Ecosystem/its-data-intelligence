from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Text, UniqueConstraint
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

    __table_args__ = (Index("ix_traffic_events_camera_ts", "camera_id", ts.desc()),)


class TrafficMetric(Base):
    __tablename__ = "traffic_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vehicle_count: Mapped[int | None] = mapped_column(Integer)
    counts_by_class: Mapped[str | None] = mapped_column(Text)  # JSON string
    avg_speed_kmh: Mapped[float | None] = mapped_column(Float)
    stopped_ratio: Mapped[float | None] = mapped_column(Float)
    queue_length: Mapped[int | None] = mapped_column(Integer)
    congestion_level: Mapped[str | None] = mapped_column(Text)
    congestion_score: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("camera_id", "window_start"),
        Index("ix_traffic_metrics_camera_window", "camera_id", window_start.desc()),
    )
