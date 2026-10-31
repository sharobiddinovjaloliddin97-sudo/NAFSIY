import asyncio
from contextlib import asynccontextmanager
import hmac
import posixpath
import re
import urllib.parse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.api.quran import router as quran_router
from backend.api.prayer import router as prayer_router
from backend.api.hadith import router as hadith_router
from backend.api.ai import router as ai_router
from backend.api.auth import router as auth_router
from backend.api.user_data import router as user_data_router
from backend.db.database import engine
from backend.db import models
from backend.db import auth_models
from backend.db import user_data_models
from backend.db import ai_models
from backend.db import idempotency_models
from backend.db import lease_models
from backend.db import auth_security_models
from backend.db import operations_models
from backend.core.auth_maintenance import cleanup_loop
from backend.core.ai_maintenance import cleanup_loop as ai_cleanup_loop
from backend.ai.generation import settle
from backend.core.config import settings
from backend.core.deployment import validate_staging
from backend.core.http_monitor import HTTPMonitor
import logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_staging(settings)
    logging.getLogger("nafsiy.http").setLevel(logging.INFO)
    if not logging.getLogger("nafsiy.http").handlers:
        logging.getLogger("nafsiy.http").addHandler(logging.StreamHandler())
    task = asyncio.create_task(cleanup_loop(getattr(app.state, "auth_maintenance_engine", engine)))
    ai_task = asyncio.create_task(ai_cleanup_loop(getattr(app.state, "auth_maintenance_engine", engine)))
    try:
        yield
    finally:
        task.cancel()
        ai_task.cancel()
        try:
            await settle(task)
        except asyncio.CancelledError:
            pass
        try:
            await settle(ai_task)
        except asyncio.CancelledError:
            pass

docs_url = "/docs" if settings.APP_ENV == "development" else None
redoc_url = "/redoc" if settings.APP_ENV == "development" else None
openapi_url = "/openapi.json" if settings.APP_ENV == "development" else None

app = FastAPI(
    title="NAFSIY API",
    description="Intelligent Islamic Companion",
    version="0.3.0",
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token", "X-CSRF-Request"],
    expose_headers=["Retry-After"],
)

app.add_middleware(HTTPMonitor)

app.include_router(quran_router)
app.include_router(prayer_router)
app.include_router(hadith_router)
app.include_router(ai_router)
app.include_router(auth_router)
app.include_router(user_data_router)


@app.get("/")
def home():
    return {
        "project": "NAFSIY",
        "status": "running",
        "version": "0.3.0"
    }


@app.get("/api/v1/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            if settings.APP_ENV == "staging":
                from backend.db.readiness import head
                if connection.scalar(text("SELECT version_num FROM alembic_version")) != head():
                    raise HTTPException(503, "Database revision unavailable")
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )
    return {"status": "healthy", "database": "connected"}


@app.middleware("http")
async def security_headers(request, call_next):
    if settings.APP_ENV == "staging" and settings.STAGING_PROXY_SECRET:
        raw_path = request.url.path.split("?")[0]
        unquoted = urllib.parse.unquote(raw_path)
        collapsed = re.sub(r"/+", "/", unquoted)
        path = posixpath.normpath(collapsed).rstrip("/") or "/"
        if path not in ("/", "/api/v1/health"):
            incoming = request.headers.get("x-staging-proxy-secret", "")
            if not hmac.compare_digest(incoming, settings.STAGING_PROXY_SECRET):
                return JSONResponse(
                    {"detail": "Forbidden: direct access prohibited"},
                    status_code=403,
                    headers={
                        "X-Content-Type-Options": "nosniff",
                        "X-Frame-Options": "DENY",
                        "Referrer-Policy": "no-referrer",
                        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                        "Strict-Transport-Security": "max-age=31536000",
                    },
                )
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger("nafsiy.http").error('{"event":"unexpected_request_failure"}')
        response = JSONResponse({"detail": "Service temporarily unavailable"}, status_code=500)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.APP_ENV == "staging":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.url.path.startswith("/api/v1/auth"):
        response.headers["Cache-Control"] = "no-store"
    return response
