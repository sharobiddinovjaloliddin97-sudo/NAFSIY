"""Account verification, recovery and session generation revocation.

Revision ID: e6f1027a93bd
Revises: c4d8e2a9106f
"""
from alembic import op
import sqlalchemy as sa

revision = "e6f1027a93bd"
down_revision = "c4d8e2a9106f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("auth_users", sa.Column("email_verified", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("auth_users", sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False))
    op.create_table("auth_recovery_tokens",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_recovery_tokens_user_id", "auth_recovery_tokens", ["user_id"])
    op.create_index("ix_auth_recovery_tokens_expires_at", "auth_recovery_tokens", ["expires_at"])


def downgrade():
    op.drop_table("auth_recovery_tokens")
    op.drop_column("auth_users", "auth_version")
    op.drop_column("auth_users", "email_verified")
