from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


class ChatGenerationLease(Base):
    __tablename__ = "chat_generation_leases"
    __table_args__ = (CheckConstraint("version > 0", name="ck_generation_lease_version"),)

    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True,
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
