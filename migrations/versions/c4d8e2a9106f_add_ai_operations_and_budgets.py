from alembic import op
import sqlalchemy as sa


revision = "c4d8e2a9106f"
down_revision = "ade01cf7b885"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_idempotency", sa.Column("provider_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("ai_operations_gate", sa.Column("id", sa.Integer(), primary_key=True))
    op.create_table("ai_global_slots",
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_ai_global_slots_expires_at", "ai_global_slots", ["expires_at"])
    op.create_table("ai_budgets",
        sa.Column("period", sa.String(12), primary_key=True),
        sa.Column("spent_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(20, 10), nullable=False),
        sa.CheckConstraint("spent_usd >= 0 AND reserved_usd >= 0", name="ck_ai_budget_nonnegative"))
    op.create_table("ai_operations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("request_id", sa.String(32), sa.ForeignKey("ai_idempotency.id", ondelete="SET NULL")),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("slot_version", sa.BigInteger(), nullable=False),
        sa.Column("generation_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("day", sa.String(12), nullable=False),
        sa.Column("month", sa.String(12), nullable=False),
        sa.Column("remaining_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("input_price", sa.Numeric(20, 10), nullable=False),
        sa.Column("output_price", sa.Numeric(20, 10), nullable=False),
        sa.Column("input_limit", sa.Integer(), nullable=False),
        sa.Column("planner_limit", sa.Integer(), nullable=False),
        sa.Column("output_limit", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("ix_ai_operations_request_id", "ai_operations", ["request_id"])
    op.create_index("ix_ai_operations_status", "ai_operations", ["status"])
    op.create_table("ai_provider_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("operation_id", sa.String(32), sa.ForeignKey("ai_operations.id"), nullable=False),
        sa.Column("phase", sa.String(12), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_tokens", sa.Integer()), sa.Column("output_tokens", sa.Integer()),
        sa.Column("reserved_usd", sa.Numeric(20, 10), nullable=False),
        sa.Column("estimated_usd", sa.Numeric(20, 10)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("operation_id", "phase", name="uq_ai_attempt_phase"))
    op.create_index("ix_ai_provider_attempts_operation_id", "ai_provider_attempts", ["operation_id"])
    op.create_index("ix_ai_rate_limits_created_at", "ai_rate_limits", ["created_at"])
    op.create_index("ix_ai_idempotency_updated_at", "ai_idempotency", ["updated_at"])


def downgrade():
    op.drop_index("ix_ai_idempotency_updated_at", table_name="ai_idempotency")
    op.drop_index("ix_ai_rate_limits_created_at", table_name="ai_rate_limits")
    op.drop_table("ai_provider_attempts")
    op.drop_table("ai_operations")
    op.drop_table("ai_budgets")
    op.drop_table("ai_global_slots")
    op.drop_table("ai_operations_gate")
    op.drop_column("ai_idempotency", "provider_attempts")
