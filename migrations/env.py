
import os
import sys
from pathlib import Path

from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

# Allow imports from the NAFSIY project root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from backend.db.database import Base
from backend.db import models
from backend.db import auth_models
from backend.db import user_data_models
from backend.db import ai_models
from backend.db import idempotency_models
from backend.db import lease_models
from backend.db import auth_security_models


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url():
    target = context.get_x_argument(as_dictionary=True).get("database")
    if target not in (None, "test"):
        raise RuntimeError("Use -x database=test to select the migration test database")
    variable = "TEST_DATABASE_URL" if target == "test" else "DATABASE_URL"
    url = os.getenv(variable)

    if not url:
        raise RuntimeError(f"{variable} is missing")

    if not url.startswith(
        ("postgresql+psycopg://", "postgresql://")
    ):
        raise RuntimeError(
            "Alembic must use PostgreSQL"
        )

    if target == "test":
        test = make_url(url)
        main = make_url(os.environ["DATABASE_URL"])
        test_host = (test.host or "").replace("-pooler", "")
        main_host = (main.host or "").replace("-pooler", "")
        if not test_host or "-pooler" in (test.host or ""):
            raise RuntimeError("TEST_DATABASE_URL must use a direct PostgreSQL host")
        if (test_host, test.port or 5432, test.database) == (main_host, main.port or 5432, main.database):
            raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL")

    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline():
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(
        get_database_url(),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 15},
    )

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
