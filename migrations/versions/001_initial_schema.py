"""Initial schema — traffic_events and traffic_metrics tables.

Revision ID: 001
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "traffic_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Text, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vehicle_id", sa.Text),
        sa.Column("class", sa.Text),
        sa.Column("centroid_x", sa.Float),
        sa.Column("centroid_y", sa.Float),
        sa.Column("speed_kmh", sa.Float),
    )
    op.create_index("ix_traffic_events_camera_ts", "traffic_events", ["camera_id", sa.text("ts DESC")])

    op.create_table(
        "traffic_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Text, nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vehicle_count", sa.Integer),
        sa.Column("counts_by_class", sa.Text),
        sa.Column("avg_speed_kmh", sa.Float),
        sa.Column("stopped_ratio", sa.Float),
        sa.Column("queue_length", sa.Integer),
        sa.Column("congestion_level", sa.Text),
        sa.Column("congestion_score", sa.Float),
    )
    op.create_unique_constraint("uq_metrics_camera_window", "traffic_metrics", ["camera_id", "window_start"])
    op.create_index("ix_traffic_metrics_camera_window", "traffic_metrics", ["camera_id", sa.text("window_start DESC")])


def downgrade():
    op.drop_table("traffic_metrics")
    op.drop_table("traffic_events")
