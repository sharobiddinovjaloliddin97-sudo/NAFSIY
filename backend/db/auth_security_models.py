from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.database import Base


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    __table_args__ = (CheckConstraint("count >= 0", name="ck_auth_rate_count"),)

    bucket_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
