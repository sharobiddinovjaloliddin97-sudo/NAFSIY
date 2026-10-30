"""add generation leases and recovery

Revision ID: f9a045983bdf
Revises: 7bbcad9d4644
Create Date: 2026-10-31 00:22:00.282782

"""
from alembic import op
import sqlalchemy as sa


revision: str = 'f9a045983bdf'
down_revision = '7bbcad9d4644'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_idempotency", sa.Column("lease_owner", sa.String(32), nullable=True))
    op.add_column("ai_idempotency", sa.Column("lease_version", sa.BigInteger(), nullable=True))
    op.add_column("ai_idempotency", sa.Column("rate_charged", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE ai_idempotency SET rate_charged = true")
    op.create_table(
        "chat_generation_leases",
        sa.Column("session_id", sa.String(32), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_generation_lease_version"),
    )


def downgrade() -> None:
    op.drop_table("chat_generation_leases")
    op.drop_column("ai_idempotency", "rate_charged")
    op.drop_column("ai_idempotency", "lease_version")
    op.drop_column("ai_idempotency", "lease_owner")
