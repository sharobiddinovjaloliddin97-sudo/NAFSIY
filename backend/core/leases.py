from dataclasses import dataclass
from datetime import timedelta
import math
import uuid

from fastapi import HTTPException
from sqlalchemy import DateTime, extract, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.lease_models import ChatGenerationLease


LEASE_SECONDS = 30
RENEW_INTERVAL_SECONDS = 8
STATEMENT_TIMEOUT_MS = 5000
LOCK_TIMEOUT_MS = 3000
IDLE_TRANSACTION_TIMEOUT_MS = 10000


@dataclass(frozen=True)
class GenerationLease:
    session_id: str
    request_id: str
    owner_id: str
    version: int


def transaction_timeouts(db: Session):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(select(func.set_config("statement_timeout", str(STATEMENT_TIMEOUT_MS), True)))
        db.execute(select(func.set_config("lock_timeout", str(LOCK_TIMEOUT_MS), True)))
        db.execute(select(func.set_config("idle_in_transaction_session_timeout", str(IDLE_TRANSACTION_TIMEOUT_MS), True)))


def clock(db: Session):
    if db.get_bind().dialect.name == "postgresql":
        return func.clock_timestamp(type_=DateTime(timezone=True))
    return func.strftime("%Y-%m-%d %H:%M:%f", "now")


def deadline(db: Session):
    if db.get_bind().dialect.name == "postgresql":
        return clock(db) + timedelta(seconds=LEASE_SECONDS)
    return func.strftime("%Y-%m-%d %H:%M:%f", "now", f"+{LEASE_SECONDS} seconds")


def acquire_lease(db: Session, session_id: str, request_id: str, *, same_request: bool) -> GenerationLease:
    dialect = db.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise HTTPException(503, "Generation leases require a supported database")
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    owner_id = uuid.uuid4().hex
    statement = insert(ChatGenerationLease).values(
        session_id=session_id, owner_id=owner_id, request_id=request_id,
        version=1, expires_at=deadline(db), updated_at=clock(db),
    ).on_conflict_do_update(
        index_elements=[ChatGenerationLease.session_id],
        set_={"owner_id": owner_id, "request_id": request_id,
              "version": ChatGenerationLease.version + 1,
              "expires_at": deadline(db), "updated_at": clock(db)},
        where=ChatGenerationLease.expires_at <= clock(db),
    ).returning(ChatGenerationLease.version)
    version = db.execute(statement).scalar_one_or_none()
    if version is None:
        if dialect == "postgresql":
            seconds = extract("epoch", ChatGenerationLease.expires_at - clock(db))
        else:
            seconds = (func.julianday(ChatGenerationLease.expires_at) - func.julianday("now")) * 86400
        remaining = db.scalar(select(seconds).where(ChatGenerationLease.session_id == session_id))
        raise HTTPException(
            409, "Request is already processing" if same_request else "Chat is busy; retry later",
            headers={"Retry-After": str(max(1, math.ceil(float(remaining or 1))))},
        )
    return GenerationLease(session_id, request_id, owner_id, version)


def owned(lease: GenerationLease):
    return (
        ChatGenerationLease.session_id == lease.session_id,
        ChatGenerationLease.request_id == lease.request_id,
        ChatGenerationLease.owner_id == lease.owner_id,
        ChatGenerationLease.version == lease.version,
    )


def fence_lease(db: Session, lease: GenerationLease):
    changed = db.execute(update(ChatGenerationLease).where(
        *owned(lease), ChatGenerationLease.expires_at > clock(db),
    ).values(expires_at=deadline(db), updated_at=clock(db)))
    if changed.rowcount != 1:
        from backend.core.ai_operations import event
        event("fencing_rejected", phase="chat")
        raise HTTPException(409, "Generation lease lost; retry with the same Idempotency-Key", headers={"Retry-After": "1"})


def renew_lease(db: Session, lease: GenerationLease):
    transaction_timeouts(db)
    fence_lease(db, lease)
    db.commit()


def release_lease(db: Session, lease: GenerationLease):
    db.execute(update(ChatGenerationLease).where(*owned(lease)).values(
        expires_at=clock(db), updated_at=clock(db),
    ))
