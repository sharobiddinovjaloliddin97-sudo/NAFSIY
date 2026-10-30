from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.engine import make_url

from backend.core.config import settings

engine_options = {"pool_timeout": settings.DB_POOL_TIMEOUT_SECONDS, "pool_size": settings.DB_POOL_SIZE,
                  "max_overflow": settings.DB_MAX_OVERFLOW, "pool_recycle": settings.DB_POOL_RECYCLE_SECONDS,
                  "pool_use_lifo": True, "connect_args": {"connect_timeout": 10}} if (
    make_url(settings.DATABASE_URL).get_backend_name() == "postgresql"
) else {}
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session
