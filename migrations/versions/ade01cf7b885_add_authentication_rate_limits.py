"""add authentication rate limits

Revision ID: ade01cf7b885
Revises: f9a045983bdf
Create Date: 2026-10-31 01:02:26.862718

"""
from alembic import op
import sqlalchemy as sa


revision: str = 'ade01cf7b885'
down_revision = 'f9a045983bdf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_limits",
        sa.Column("bucket_key", sa.String(128), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("count >= 0", name="ck_auth_rate_count"),
    )
    op.create_index("ix_auth_rate_limits_expires_at", "auth_rate_limits", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_rate_limits_expires_at", table_name="auth_rate_limits")
    op.drop_table("auth_rate_limits")
