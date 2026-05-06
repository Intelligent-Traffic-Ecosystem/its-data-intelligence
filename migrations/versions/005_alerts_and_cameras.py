"""Add cameras and alerts tables for B3 dashboard endpoints.

Revision ID: 005
Revises: 004
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("road_segment", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_cameras_road_segment", "cameras", ["road_segment"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("camera_id", sa.Text),
        sa.Column("road_segment", sa.Text),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("message", sa.Text),
        sa.Column("congestion_level", sa.Text),
        sa.Column("congestion_score", sa.Float),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.Text),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("payload", sa.Text),
    )
    op.create_index(
        "ix_alerts_triggered_at",
        "alerts",
        [sa.text("triggered_at DESC")],
    )
    op.create_index(
        "ix_alerts_severity_triggered_at",
        "alerts",
        ["severity", sa.text("triggered_at DESC")],
    )
    op.create_index(
        "ix_alerts_camera_triggered_at",
        "alerts",
        ["camera_id", sa.text("triggered_at DESC")],
    )
    op.create_index(
        "ix_alerts_road_segment_triggered_at",
        "alerts",
        ["road_segment", sa.text("triggered_at DESC")],
    )
    op.create_index(
        "ix_alerts_open",
        "alerts",
        ["camera_id", "alert_type", "resolved_at"],
    )


def downgrade():
    op.drop_index("ix_alerts_open", table_name="alerts")
    op.drop_index("ix_alerts_road_segment_triggered_at", table_name="alerts")
    op.drop_index("ix_alerts_camera_triggered_at", table_name="alerts")
    op.drop_index("ix_alerts_severity_triggered_at", table_name="alerts")
    op.drop_index("ix_alerts_triggered_at", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_cameras_road_segment", table_name="cameras")
    op.drop_table("cameras")
