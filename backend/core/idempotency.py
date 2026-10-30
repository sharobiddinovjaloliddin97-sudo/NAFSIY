import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.idempotency_models import AIIdempotency
from backend.db.user_data_models import ChatSession
from backend.core.leases import GenerationLease, acquire_lease, clock, release_lease, transaction_timeouts
from backend.core.rate_limit import check_ai_rate_limit


def fingerprint(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def reserve_request(
    db: Session, user_id: str, session_id: str, request_key: str, message: str,
) -> dict:
    try:
        normalized_key = str(uuid.UUID(request_key))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, "Idempotency-Key must be a UUID") from None
    row = db.scalar(select(AIIdempotency).where(
        AIIdempotency.user_id == user_id, AIIdempotency.session_id == session_id,
        AIIdempotency.request_key == normalized_key,
    ))
    request_hash = fingerprint(message)
    if row is not None:
        if row.request_hash != request_hash:
            raise HTTPException(409, "Idempotency-Key was already used for a different message")
        if row.status == "completed":
            try:
                return {"state": "replay", "response": json.loads(row.response_json)}
            except (TypeError, ValueError):
                raise HTTPException(503, "Saved AI response is unavailable") from None
    request_id = row.id if row is not None else uuid.uuid4().hex
    lease = acquire_lease(db, session_id, request_id, same_request=row is not None)
    if row is None:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        inserted = db.execute(insert(AIIdempotency).values(
            id=request_id, user_id=user_id, session_id=session_id, request_key=normalized_key,
            request_hash=request_hash, status="processing", rate_charged=False,
        ).on_conflict_do_nothing(
            index_elements=[AIIdempotency.user_id, AIIdempotency.session_id, AIIdempotency.request_key],
        ).returning(AIIdempotency.id)).scalar_one_or_none()
        if inserted is None:
            raise HTTPException(409, "Request state changed; retry with the same Idempotency-Key", headers={"Retry-After": "1"})
    if row is None or not row.rate_charged:
        check_ai_rate_limit(db, user_id, commit=False)
    db.execute(update(AIIdempotency).where(AIIdempotency.id == request_id).values(
        lease_owner=lease.owner_id, lease_version=lease.version, rate_charged=True, updated_at=clock(db),
    ))
    return {"state": "reserved", "id": request_id, "lease": lease}


def complete_request(db: Session, lease: GenerationLease, response: dict):
    changed = db.execute(update(AIIdempotency).where(
        AIIdempotency.id == lease.request_id, AIIdempotency.status == "processing",
        AIIdempotency.lease_owner == lease.owner_id, AIIdempotency.lease_version == lease.version,
    ).values(
        status="completed", response_json=json.dumps(response, ensure_ascii=False),
        updated_at=clock(db),
    ))
    if changed.rowcount != 1:
        raise HTTPException(409, "AI request reservation is no longer available")


def release_failed_request(db: Session, lease: GenerationLease):
    transaction_timeouts(db)
    db.scalar(select(ChatSession.id).where(ChatSession.id == lease.session_id).with_for_update())
    release_lease(db, lease)
    db.commit()
