from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class AIIdempotency(Base):
    __tablename__ = "ai_idempotency"
    __table_args__ = (
        UniqueConstraint("user_id", "session_id", "request_key", name="uq_ai_idempotency_request"),
        CheckConstraint(
            "(status = 'processing' AND response_json IS NULL) OR "
            "(status = 'completed' AND response_json IS NOT NULL)",
            name="ck_ai_idempotency_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    request_key: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rate_charged: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
