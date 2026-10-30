
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
from backend.core.auth_maintenance import cleanup_loop
from backend.ai.generation import settle


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(cleanup_loop(getattr(app.state, "auth_maintenance_engine", engine)))
    try:
        yield
    finally:
        task.cancel()
        try:
            await settle(task)
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title="NAFSIY API",
    description="Intelligent Islamic Companion",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token", "X-CSRF-Request"],
    expose_headers=["Retry-After"],
)

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
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )
    return {"status": "healthy", "database": "connected"}
