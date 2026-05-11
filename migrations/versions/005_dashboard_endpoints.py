"""Add cameras registry and persisted alerts for B3.

Revision ID: 005
Revises: 004
Create Date: 2026-05-11
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cameras",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("road_segment", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("camera_id", sa.Text, nullable=False),
        sa.Column("road_segment", sa.Text),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("congestion_level", sa.Text),
        sa.Column("congestion_score", sa.Float),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.Text),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("payload", sa.Text),
    )
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index("ix_alerts_road_segment", "alerts", ["road_segment"])
    op.create_index("ix_alerts_alert_type", "alerts", ["alert_type"])
    op.create_index("ix_alerts_camera_id", "alerts", ["camera_id"])
    op.create_index("ix_alerts_triggered_at", "alerts", ["triggered_at"])
    op.create_index("ix_alerts_resolved_at", "alerts", ["resolved_at"])


def downgrade():
    op.drop_index("ix_alerts_resolved_at", table_name="alerts")
    op.drop_index("ix_alerts_triggered_at", table_name="alerts")
    op.drop_index("ix_alerts_camera_id", table_name="alerts")
    op.drop_index("ix_alerts_alert_type", table_name="alerts")
    op.drop_index("ix_alerts_road_segment", table_name="alerts")
    op.drop_index("ix_alerts_severity", table_name="alerts")
    op.drop_table("alerts")
    op.drop_table("cameras")
