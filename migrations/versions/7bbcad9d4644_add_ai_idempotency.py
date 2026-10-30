"""add ai idempotency

Revision ID: 7bbcad9d4644
Revises: 69e1d999085f
Create Date: 2026-10-30 23:59:13.999523

"""
from alembic import op
import sqlalchemy as sa


revision: str = '7bbcad9d4644'
down_revision = '69e1d999085f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_idempotency",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(32), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "session_id", "request_key", name="uq_ai_idempotency_request"),
        sa.CheckConstraint(
            "(status = 'processing' AND response_json IS NULL) OR "
            "(status = 'completed' AND response_json IS NOT NULL)",
            name="ck_ai_idempotency_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_idempotency")
