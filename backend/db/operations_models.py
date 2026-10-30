from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


class AIOperationsGate(Base):
    __tablename__ = "ai_operations_gate"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class AIGlobalSlot(Base):
    __tablename__ = "ai_global_slots"
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AIBudget(Base):
    __tablename__ = "ai_budgets"
    __table_args__ = (CheckConstraint("spent_usd >= 0 AND reserved_usd >= 0", name="ck_ai_budget_nonnegative"),)
    period: Mapped[str] = mapped_column(String(12), primary_key=True)
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False, default=Decimal(0))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False, default=Decimal(0))


class AIOperation(Base):
    __tablename__ = "ai_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("ai_idempotency.id", ondelete="SET NULL"), index=True)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    day: Mapped[str] = mapped_column(String(12), nullable=False)
    month: Mapped[str] = mapped_column(String(12), nullable=False)
    remaining_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    input_price: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    output_price: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    input_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    planner_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    output_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIProviderAttempt(Base):
    __tablename__ = "ai_provider_attempts"
    __table_args__ = (UniqueConstraint("operation_id", "phase", name="uq_ai_attempt_phase"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(32), ForeignKey("ai_operations.id"), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(12), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="groq")
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    estimated_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
