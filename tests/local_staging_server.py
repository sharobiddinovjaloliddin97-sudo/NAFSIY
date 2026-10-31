"""Loopback-only synthetic fixture. No external DB/email/provider; deletion disabled."""
import asyncio
import os
from pathlib import Path
import sys

if os.getenv("NAFSIY_LOCAL_STAGING_CHECK") != "1":
    raise SystemExit("Explicit local staging opt-in required")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Override before importing Settings: never initialize an engine for the real .env.
os.environ.update(DATABASE_URL="sqlite:///" + os.environ["NAFSIY_FIXTURE_DB"],
                  APP_ENV="development", JWT_SECRET_KEY="local-synthetic-staging-check-key-" * 2,
                  GROQ_API_KEY="synthetic-unused", EMAIL_MODE="disabled",
                  SMTP_PASSWORD="synthetic-unused", ACCOUNT_DELETION_ENABLED="false",
                  AUTH_COOKIE_SECURE="true", REQUIRE_EMAIL_VERIFICATION="true",
                  AUTH_TRUSTED_ORIGINS='["https://localhost:3443"]',
                  CORS_ORIGINS='["https://localhost:3443"]', MAX_GLOBAL_AI_GENERATIONS="2")

from fastapi import HTTPException
from sqlalchemy.orm import Session
import uvicorn
from backend.main import app
from backend.api import ai, auth
from backend.db.database import Base, engine
from backend.db.auth_models import User

Base.metadata.create_all(engine)
encoded = auth.password_hash.hash("Synthetic local password 123!")
with Session(engine) as db:
    db.add_all(User(email=f"load{i}@example.com", password_hash=encoded,
                    email_verified=True) for i in range(4))
    db.commit()
app.state.auth_maintenance_engine = engine


async def synthetic(question, history=None):
    if question == "slow proxy probe":
        await asyncio.sleep(31)
    elif question == "upstream failure":
        raise RuntimeError("synthetic-private-upstream-detail")
    else:
        await asyncio.sleep(0.15)
    return {"answer": "Synthetic answer", "sources": [], "model": "local-fixture"}


ai.ask_nafsiy = synthetic
assert auth.settings.ACCOUNT_DELETION_ENABLED is False
try:
    uvicorn.run(app, host="127.0.0.1", port=8103, proxy_headers=False,
                access_log=False, log_level="warning")
finally:
    engine.dispose()
