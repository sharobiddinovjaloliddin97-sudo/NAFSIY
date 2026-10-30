import time

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.ai_models import AIRateLimit


MAX_AI_REQUESTS = 5
WINDOW_SECONDS = 60


def check_ai_rate_limit(db: Session, user_id: str):
    now = int(time.time())
    window = now // WINDOW_SECONDS
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        insert = pg_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise HTTPException(503, "AI rate limiter requires a supported database")
    statement = insert(AIRateLimit).values(bucket_key=f"{user_id}:{window}", count=1)
    statement = statement.on_conflict_do_update(
        index_elements=[AIRateLimit.bucket_key],
        set_={"count": AIRateLimit.count + 1},
        where=AIRateLimit.count < MAX_AI_REQUESTS,
    ).returning(AIRateLimit.count)
    count = db.execute(statement).scalar_one_or_none()
    db.commit()
    if count is None:
        raise HTTPException(
            429, "AI request limit reached: 5 requests per minute",
            headers={"Retry-After": str(WINDOW_SECONDS - now % WINDOW_SECONDS)},
        )
