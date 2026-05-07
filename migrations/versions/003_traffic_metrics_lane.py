"""Add lane_id to traffic_metrics for per-lane breakdowns.

Revision ID: 003
Revises: 002
Create Date: 2026-04-27

Adds nullable lane_id to traffic_metrics. A row with lane_id=NULL is the
camera-wide aggregate; rows with lane_id IS NOT NULL are per-lane breakdowns.

Replaces the (camera_id, window_start) unique constraint with a unique
expression index on (camera_id, COALESCE(lane_id, -1), window_start) so a
camera-wide row and per-lane rows can coexist for the same window.
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():#agan new features addng
    op.add_column("traffic_metrics", sa.Column("lane_id", sa.Integer))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("uq_metrics_camera_window", "traffic_metrics", type_="unique")
        op.execute(
            "CREATE UNIQUE INDEX uq_metrics_camera_lane_window "
            "ON traffic_metrics (camera_id, COALESCE(lane_id, -1), window_start)"#lane_id NULL
        )
    else:
        op.drop_constraint("uq_metrics_camera_window", "traffic_metrics", type_="unique")
        op.create_unique_constraint(
            "uq_metrics_camera_lane_window",
            "traffic_metrics",
            ["camera_id", "lane_id", "window_start"],
        )


def downgrade():#Downgrade (rollback) go to old version 
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_metrics_camera_lane_window")
    else:
        op.drop_constraint("uq_metrics_camera_lane_window", "traffic_metrics", type_="unique")

    op.create_unique_constraint(
        "uq_metrics_camera_window", "traffic_metrics", ["camera_id", "window_start"]
    )
    op.drop_column("traffic_metrics", "lane_id")
