from datetime import datetime, timezone
import hashlib
import hmac
import time

from fastapi import HTTPException, Request
from sqlalchemy import delete, extract, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.auth_tokens import security_key
from backend.core.leases import transaction_timeouts
from backend.core.request_security import rate_ip
from backend.db.auth_security_models import AuthRateLimit


LOGIN_IP_LIMIT = 30
LOGIN_PAIR_LIMIT = 5
LOGIN_WINDOW = 300
REGISTER_IP_LIMIT = 5
REGISTER_PAIR_LIMIT = 3
REGISTER_WINDOW = 3600
BOOTSTRAP_LIMIT = 60
BOOTSTRAP_WINDOW = 60
RETENTION_GRACE = 600
CLEANUP_BATCH = 500


def digest(scope: str, subject: str) -> str:
    return hmac.new(security_key("auth-rate"), (scope + "\0" + subject).encode(), hashlib.sha256).hexdigest()


def epoch(db: Session) -> int:
    if db.get_bind().dialect.name == "postgresql":
        return int(db.scalar(select(extract("epoch", func.clock_timestamp()))))
    return int(time.time())


def purge_expired(db: Session) -> int:
    try:
        transaction_timeouts(db)
        removed = _purge(db, epoch(db))
        db.commit()
        return removed
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication limiter maintenance unavailable") from None


def _purge(db: Session, now: int) -> int:
    expired = select(AuthRateLimit.bucket_key).where(
        AuthRateLimit.expires_at <= datetime.fromtimestamp(now, timezone.utc),
    ).order_by(AuthRateLimit.expires_at).limit(CLEANUP_BATCH)
    return db.execute(delete(AuthRateLimit).where(AuthRateLimit.bucket_key.in_(expired))).rowcount


def take_slot(db: Session, scope: str, subject: str, limit: int, window: int) -> str:
    try:
        transaction_timeouts(db)
        now = epoch(db)
        end = (now // window + 1) * window
        key = f"{scope}:{digest(scope, subject)}:{now // window}"
        _purge(db, now)
        dialect = db.get_bind().dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise HTTPException(503, "Authentication limiter unavailable")
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        statement = insert(AuthRateLimit).values(
            bucket_key=key, count=1,
            expires_at=datetime.fromtimestamp(end + RETENTION_GRACE, timezone.utc),
        ).on_conflict_do_update(
            index_elements=[AuthRateLimit.bucket_key], set_={"count": AuthRateLimit.count + 1},
            where=AuthRateLimit.count < limit,
        ).returning(AuthRateLimit.count)
        count = db.execute(statement).scalar_one_or_none()
        db.commit()
        if count is None:
            raise HTTPException(429, "Too many authentication attempts", headers={
                "Retry-After": str(max(1, end - now)), "Cache-Control": "no-store",
            })
        return key
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication limiter unavailable", headers={"Cache-Control": "no-store"}) from None


def login_admission(db: Session, request: Request, email: str) -> str:
    ip = rate_ip(request)
    take_slot(db, "login-ip", ip, LOGIN_IP_LIMIT, LOGIN_WINDOW)
    return take_slot(db, "login-pair", email + "\0" + ip, LOGIN_PAIR_LIMIT, LOGIN_WINDOW)


def registration_admission(db: Session, request: Request, email: str):
    ip = rate_ip(request)
    take_slot(db, "register-ip", ip, REGISTER_IP_LIMIT, REGISTER_WINDOW)
    take_slot(db, "register-pair", email + "\0" + ip, REGISTER_PAIR_LIMIT, REGISTER_WINDOW)


def refund_successful_login(db: Session, bucket_key: str):
    try:
        transaction_timeouts(db)
        db.execute(update(AuthRateLimit).where(
            AuthRateLimit.bucket_key == bucket_key, AuthRateLimit.count > 0,
        ).values(count=AuthRateLimit.count - 1))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication limiter unavailable", headers={"Cache-Control": "no-store"}) from None
