
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.api.quran import router as quran_router
from backend.api.prayer import router as prayer_router
from backend.db.database import Base, engine
from backend.db import models  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="NAFSIY API",
    description="Intelligent Islamic Companion",
    version="0.3.0",
    lifespan=lifespan
)

app.include_router(quran_router)
app.include_router(prayer_router)


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
