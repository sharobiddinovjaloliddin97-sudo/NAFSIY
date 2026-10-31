"""Hashed bearer tokens; atomic single-use consumption and credential fencing."""
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.core.email_delivery import delivery
from backend.db.auth_models import RecoveryToken, User


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def request_link(db, email, purpose):
    try:
        user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
        if user is None or (purpose == "verify" and user.email_verified):
            db.rollback()
            return
        raw = secrets.token_urlsafe(32)
        address = user.email
        now = datetime.now(timezone.utc)
        db.execute(delete(RecoveryToken).where(RecoveryToken.digest.in_(
            select(RecoveryToken.digest).where(RecoveryToken.expires_at <= now).limit(500))))
        db.add(RecoveryToken(digest=digest(raw), user_id=user.id, purpose=purpose,
                             version=user.auth_version,
                             expires_at=now + timedelta(minutes=30 if purpose == "reset" else 60)))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Account recovery unavailable") from None
    try:
        delivery().send(address, purpose, raw)
    except Exception:
        # Never serialize SMTP errors, message bodies, tokens, recipient or credentials.
        logging.getLogger(__name__).warning("email_delivery_failed")


def consume(db, token, purpose, password_hash=None):
    try:
        row = db.execute(delete(RecoveryToken).where(
            RecoveryToken.digest == digest(token), RecoveryToken.purpose == purpose,
            RecoveryToken.expires_at > datetime.now(timezone.utc),
        ).returning(RecoveryToken.user_id, RecoveryToken.version)).first()
        if row is None:
            raise HTTPException(400, "Invalid or expired recovery token")
        values = {"email_verified": True} if purpose == "verify" else {
            "password_hash": password_hash, "auth_version": User.auth_version + 1,
        }
        changed = db.execute(update(User).where(
            User.id == row.user_id, User.is_active.is_(True), User.auth_version == row.version,
        ).values(**values)).rowcount
        if changed != 1:
            raise HTTPException(400, "Invalid or expired recovery token")
        if purpose == "reset":
            db.execute(delete(RecoveryToken).where(RecoveryToken.user_id == row.user_id))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Account recovery unavailable") from None
