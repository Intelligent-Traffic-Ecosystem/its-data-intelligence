"""Extend traffic_events with full B1 schema fidelity.

Revision ID: 002
Revises: 001
Create Date: 2026-04-27

Adds frame_id, confidence, bbox_x/y/w/h, lane_id columns to traffic_events
so every field B1 publishes survives ingestion (per B1 SRS section 4.2).
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("traffic_events", sa.Column("frame_id", sa.BigInteger))
    op.add_column("traffic_events", sa.Column("confidence", sa.Float))
    op.add_column("traffic_events", sa.Column("bbox_x", sa.Integer))
    op.add_column("traffic_events", sa.Column("bbox_y", sa.Integer))
    op.add_column("traffic_events", sa.Column("bbox_w", sa.Integer))
    op.add_column("traffic_events", sa.Column("bbox_h", sa.Integer))
    op.add_column("traffic_events", sa.Column("lane_id", sa.Integer))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_traffic_events_camera_lane_ts "
            "ON traffic_events (camera_id, lane_id, ts DESC) "
            "WHERE lane_id IS NOT NULL"
        )
    else:
        op.create_index(
            "ix_traffic_events_camera_lane_ts",
            "traffic_events",
            ["camera_id", "lane_id", sa.text("ts DESC")],
        )


def downgrade():
    op.drop_index("ix_traffic_events_camera_lane_ts", table_name="traffic_events")
    op.drop_column("traffic_events", "lane_id")
    op.drop_column("traffic_events", "bbox_h")
    op.drop_column("traffic_events", "bbox_w")
    op.drop_column("traffic_events", "bbox_y")
    op.drop_column("traffic_events", "bbox_x")
    op.drop_column("traffic_events", "confidence")
    op.drop_column("traffic_events", "frame_id")
