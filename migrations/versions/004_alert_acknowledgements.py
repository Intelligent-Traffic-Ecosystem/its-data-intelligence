"""Create alert acknowledgement logs.

Revision ID: 004
Revises: 003
Create Date: 2026-05-07
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "alert_acknowledgements",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("alert_id", sa.Text, nullable=False, unique=True),
        sa.Column("admin_id", sa.Text, nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_alert_acknowledgements_alert_id",
        "alert_acknowledgements",
        ["alert_id"],
    )


def downgrade():
    op.drop_index("ix_alert_acknowledgements_alert_id", table_name="alert_acknowledgements")
    op.drop_table("alert_acknowledgements")
