"""Allow nullable camera coordinates for staged setup.

Revision ID: 006
Revises: 005
Create Date: 2026-05-11
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("cameras", "latitude", existing_type=sa.Float(), nullable=True)
    op.alter_column("cameras", "longitude", existing_type=sa.Float(), nullable=True)


def downgrade():
    op.alter_column("cameras", "longitude", existing_type=sa.Float(), nullable=False)
    op.alter_column("cameras", "latitude", existing_type=sa.Float(), nullable=False)
