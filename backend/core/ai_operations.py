import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.leases import clock, deadline, fence_lease, owned, transaction_timeouts
from backend.db.ai_models import AIRateLimit
from backend.db.idempotency_models import AIIdempotency
from backend.db.lease_models import ChatGenerationLease
from backend.db.operations_models import AIBudget, AIGlobalSlot, AIOperation, AIOperationsGate, AIProviderAttempt
from backend.db.user_data_models import ChatSession


logger = logging.getLogger("nafsiy.ai.operations")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.propagate = False
ZERO = Decimal("0")
PRECISION = Decimal("0.0000000001")
REPLAY_DAYS = 30
CLEANUP_BATCH = 100


def event(name, **values):
    logger.info(json.dumps({"event": name, "timestamp": datetime.now(timezone.utc).isoformat(), **values}, sort_keys=True))


def now(db):
    value = db.scalar(select(clock(db)))
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def gate(db):
    transaction = (db.get_transaction(), db.get_nested_transaction())
    if transaction[0] is not None and db.info.get("nafsiy_operations_gate") == transaction:
        return
    transaction_timeouts(db)
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    if db.get_bind().dialect.name == "postgresql":
        db.execute(select(func.set_config("lock_timeout", "15000", True)))
        db.execute(select(func.set_config("statement_timeout", "20000", True)))
    db.execute(insert(AIOperationsGate).values(id=1).on_conflict_do_nothing())
    db.scalar(select(AIOperationsGate.id).where(AIOperationsGate.id == 1).with_for_update())
    transaction_timeouts(db)
    db.info["nafsiy_operations_gate"] = (db.get_transaction(), db.get_nested_transaction())


def cost(inputs, outputs, input_price, output_price):
    return ((Decimal(inputs) * input_price + Decimal(outputs) * output_price) / Decimal(1000000)).quantize(PRECISION, rounding=ROUND_UP)


def budget_rows(db, operation):
    return [db.get(AIBudget, key) for key in (operation.day, operation.month)]


def settle_attempt(db, operation, attempt, *, status, inputs=None, outputs=None):
    if attempt.status != "started":
        return False
    input_known = type(inputs) is int and inputs >= 0
    output_known = type(outputs) is int and outputs >= 0
    known = input_known and output_known
    measured = cost(inputs if input_known else 0, outputs if output_known else 0, operation.input_price, operation.output_price)
    charged = measured if known else max(attempt.reserved_usd, measured)
    for row in budget_rows(db, operation):
        row.reserved_usd -= attempt.reserved_usd
        row.spent_usd += charged
    operation.remaining_usd -= attempt.reserved_usd
    attempt.status = status if known else status + "_estimated"
    attempt.input_tokens = inputs if input_known else None
    attempt.output_tokens = outputs if output_known else None
    attempt.estimated_usd = charged
    attempt.finished_at = now(db)
    db.flush()
    event("provider_usage", status=attempt.status, input_tokens=attempt.input_tokens,
          output_tokens=attempt.output_tokens, estimated_usd=str(charged), provider="groq")
    return True


def finish(db, operation, status):
    if operation.status != "active":
        return
    pending = db.scalars(select(AIProviderAttempt).where(
        AIProviderAttempt.operation_id == operation.id, AIProviderAttempt.status == "started",
    )).all()
    for attempt in pending:
        settle_attempt(db, operation, attempt, status="unknown")
    for row in budget_rows(db, operation):
        row.reserved_usd -= operation.remaining_usd
    operation.remaining_usd = ZERO
    operation.status = status
    operation.finished_at = now(db)
    db.execute(update(AIGlobalSlot).where(
        AIGlobalSlot.slot == operation.slot, AIGlobalSlot.owner == operation.id,
        AIGlobalSlot.version == operation.slot_version,
    ).values(expires_at=clock(db)))
    db.flush()


def reap(db):
    expired = db.scalars(select(AIOperation).join(AIGlobalSlot, AIOperation.slot == AIGlobalSlot.slot).where(
        AIOperation.status == "active",
        (AIGlobalSlot.expires_at <= clock(db)) | (AIGlobalSlot.owner != AIOperation.id)
        | (AIGlobalSlot.version != AIOperation.slot_version),
    ).order_by(AIOperation.created_at).limit(CLEANUP_BATCH)).all()
    for operation in expired:
        finish(db, operation, "expired")
    if expired:
        event("generation_recovery", expired=len(expired))
    return len(expired)


def admit(db: Session, lease):
    gate(db)
    reap(db)
    moment = now(db)
    day, month = moment.strftime("d:%Y-%m-%d"), moment.strftime("m:%Y-%m")
    estimate = sum(cost(settings.AI_MAX_INPUT_TOKENS, outputs,
                        settings.AI_INPUT_USD_PER_MILLION, settings.AI_OUTPUT_USD_PER_MILLION)
                   for outputs in (settings.AI_PLANNER_OUTPUT_TOKENS, settings.AI_MAX_OUTPUT_TOKENS))
    for key, limit in ((day, settings.AI_DAILY_BUDGET_USD), (month, settings.AI_MONTHLY_BUDGET_USD)):
        row = db.get(AIBudget, key)
        if row is None:
            row = AIBudget(period=key, spent_usd=ZERO, reserved_usd=ZERO)
            db.add(row)
        if row.spent_usd + row.reserved_usd + estimate > limit:
            event("request_rejected", reason="budget", period=key[:1])
            raise HTTPException(429, "AI spending budget unavailable", headers={"Retry-After": "60"})
        row.reserved_usd += estimate
    count = db.scalar(select(func.count()).select_from(AIGlobalSlot).where(AIGlobalSlot.expires_at > clock(db)))
    if count >= settings.MAX_GLOBAL_AI_GENERATIONS:
        event("request_rejected", reason="capacity", active=count)
        raise HTTPException(429, "AI global capacity exhausted", headers={"Retry-After": "5"})
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    acquired = None
    for slot in range(1, settings.MAX_GLOBAL_AI_GENERATIONS + 1):
        version = db.execute(insert(AIGlobalSlot).values(
            slot=slot, owner=lease.owner_id, version=1, expires_at=deadline(db),
        ).on_conflict_do_update(index_elements=[AIGlobalSlot.slot], set_={
            "owner": lease.owner_id, "version": AIGlobalSlot.version + 1, "expires_at": deadline(db),
        }, where=AIGlobalSlot.expires_at <= clock(db)).returning(AIGlobalSlot.version)).scalar_one_or_none()
        if version is not None:
            acquired = (slot, version)
            break
    if acquired is None:
        raise HTTPException(429, "AI global capacity exhausted", headers={"Retry-After": "5"})
    db.add(AIOperation(
        id=lease.owner_id, request_id=lease.request_id, slot=acquired[0], slot_version=acquired[1], generation_version=lease.version,
        status="active", day=day, month=month, remaining_usd=estimate,
        input_price=settings.AI_INPUT_USD_PER_MILLION, output_price=settings.AI_OUTPUT_USD_PER_MILLION,
        input_limit=settings.AI_MAX_INPUT_TOKENS, planner_limit=settings.AI_PLANNER_OUTPUT_TOKENS,
        output_limit=settings.AI_MAX_OUTPUT_TOKENS, model=settings.GROQ_MODEL, created_at=moment,
    ))
    db.flush()
    return {"active": count + 1, "reserved_usd": str(estimate)}


def owned_operation(db, lease):
    gate(db)
    operation = db.get(AIOperation, lease.owner_id)
    slot = db.scalar(select(AIGlobalSlot.slot).where(
        AIGlobalSlot.owner == lease.owner_id, AIGlobalSlot.expires_at > clock(db),
        AIGlobalSlot.version == (operation.slot_version if operation is not None else -1),
    ))
    if operation is None or operation.status != "active" or slot != operation.slot or operation.generation_version != lease.version:
        event("fencing_rejected", phase="operations")
        raise HTTPException(409, "AI capacity lease lost; retry with the same Idempotency-Key", headers={"Retry-After": "1"})
    return operation


def renew(db, lease):
    version = select(AIOperation.slot_version).where(
        AIOperation.id == lease.owner_id, AIOperation.status == "active",
        AIOperation.generation_version == lease.version, AIOperation.slot == AIGlobalSlot.slot,
    ).correlate(AIGlobalSlot).scalar_subquery()
    changed = db.execute(update(AIGlobalSlot).where(
        AIGlobalSlot.owner == lease.owner_id, AIGlobalSlot.version == version,
        AIGlobalSlot.expires_at > clock(db),
    ).values(expires_at=deadline(db)))
    if changed.rowcount != 1:
        event("fencing_rejected", phase="capacity_renewal")
        raise HTTPException(409, "AI capacity lease lost; retry with the same Idempotency-Key", headers={"Retry-After": "1"})


def activate(db, lease):
    chat = db.execute(update(ChatGenerationLease).where(*owned(lease)).values(
        expires_at=deadline(db), updated_at=clock(db),
    ))
    capacity = db.execute(update(AIGlobalSlot).where(AIGlobalSlot.owner == lease.owner_id).values(expires_at=deadline(db)))
    if chat.rowcount != 1 or capacity.rowcount != 1:
        raise HTTPException(409, "AI admission ownership changed", headers={"Retry-After": "1"})


def release(db, lease, status):
    gate(db)
    operation = db.get(AIOperation, lease.owner_id)
    if operation is not None and operation.generation_version == lease.version:
        finish(db, operation, status)


def start_attempt(db, lease, phase, input_bound, output_bound):
    fence_lease(db, lease)
    operation = owned_operation(db, lease)
    if phase not in ("planner", "answer") or input_bound > operation.input_limit or output_bound > (
        operation.planner_limit if phase == "planner" else operation.output_limit
    ) or settings.GROQ_MODEL != operation.model:
        raise HTTPException(413, "AI context or configuration exceeds reserved limits")
    count = db.execute(update(AIIdempotency).where(
        AIIdempotency.id == lease.request_id, AIIdempotency.status == "processing",
        AIIdempotency.lease_owner == lease.owner_id, AIIdempotency.lease_version == lease.version,
        AIIdempotency.provider_attempts < settings.AI_MAX_PROVIDER_ATTEMPTS,
    ).values(provider_attempts=AIIdempotency.provider_attempts + 1).returning(AIIdempotency.provider_attempts)).scalar_one_or_none()
    if count is None:
        event("request_rejected", reason="attempts")
        raise HTTPException(429, "AI provider attempt limit reached; use a new request", headers={"Retry-After": "60"})
    amount = cost(operation.input_limit,
                  operation.planner_limit if phase == "planner" else operation.output_limit,
                  operation.input_price, operation.output_price)
    attempt_id = uuid.uuid4().hex
    db.add(AIProviderAttempt(
        id=attempt_id, operation_id=operation.id, phase=phase, provider="groq", model=operation.model,
        status="started", reserved_usd=amount, started_at=now(db),
    ))
    db.commit()
    event("provider_attempt", attempt=count, phase=phase)
    return attempt_id


def record_usage(db, lease, attempt_id, status, inputs, outputs):
    fence_lease(db, lease)
    operation = owned_operation(db, lease)
    attempt = db.get(AIProviderAttempt, attempt_id)
    if attempt is None or attempt.operation_id != operation.id:
        raise HTTPException(409, "AI attempt is no longer current")
    settle_attempt(db, operation, attempt, status=status, inputs=inputs, outputs=outputs)
    db.commit()


def maintain(db):
    gate(db)
    recovered = reap(db)
    cutoff = now(db) - timedelta(minutes=10)
    old_buckets = select(AIRateLimit.bucket_key).where(AIRateLimit.created_at < cutoff).order_by(AIRateLimit.created_at).limit(CLEANUP_BATCH)
    current_window = int(now(db).timestamp()) // 60
    expired_keys = [key for key in db.scalars(old_buckets).all()
                    if key.rsplit(":", 1)[-1].isdigit() and int(key.rsplit(":", 1)[-1]) < current_window - 10]
    removed = db.execute(delete(AIRateLimit).where(AIRateLimit.bucket_key.in_(expired_keys))).rowcount
    db.commit()
    expired_records = redact_replays(db)
    event("cleanup", expired_capacity=recovered, ai_rate_buckets=removed, retired_replays=expired_records)
    return recovered + removed + expired_records


def redact_replays(db):
    marker = '{"expired": true}'
    cutoff = now(db) - timedelta(days=REPLAY_DAYS)
    candidates = db.execute(select(AIIdempotency.id, AIIdempotency.session_id).where(
        AIIdempotency.updated_at < cutoff,
        (AIIdempotency.response_json.is_(None)) | (AIIdempotency.response_json != marker),
    ).order_by(AIIdempotency.updated_at).limit(CLEANUP_BATCH)).all()
    db.rollback()
    retired = 0
    for request_id, session_id in candidates:
        transaction_timeouts(db)
        chat = db.scalar(select(ChatSession.id).where(ChatSession.id == session_id).with_for_update(skip_locked=True))
        if chat is not None:
            active = db.scalar(select(ChatGenerationLease.session_id).where(
                ChatGenerationLease.session_id == session_id, ChatGenerationLease.expires_at > clock(db),
            ))
            row = db.get(AIIdempotency, request_id)
            updated = None if row is None else row.updated_at
            if updated is not None:
                updated = updated.replace(tzinfo=timezone.utc) if updated.tzinfo is None else updated.astimezone(timezone.utc)
            if active is None and row is not None and updated < cutoff and row.response_json != marker:
                row.status = "completed"
                row.response_json = marker
                retired += 1
        db.commit()
    return retired


def snapshot(db):
    moment = now(db)
    periods = (moment.strftime("d:%Y-%m-%d"), moment.strftime("m:%Y-%m"))
    return {
        "active_generations": db.scalar(select(func.count()).select_from(AIGlobalSlot).where(AIGlobalSlot.expires_at > clock(db))),
        "capacity": settings.MAX_GLOBAL_AI_GENERATIONS,
        "daily_limit_usd": str(settings.AI_DAILY_BUDGET_USD),
        "monthly_limit_usd": str(settings.AI_MONTHLY_BUDGET_USD),
        "budgets": {key: {"spent_usd": str(row.spent_usd), "reserved_usd": str(row.reserved_usd)}
                    for key in periods if (row := db.get(AIBudget, key)) is not None},
        "usage_24h": [dict(row._mapping) for row in db.execute(select(
            AIProviderAttempt.status, func.count().label("attempts"),
            func.coalesce(func.sum(AIProviderAttempt.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AIProviderAttempt.output_tokens), 0).label("output_tokens"),
        ).where(AIProviderAttempt.started_at >= moment - timedelta(days=1)).group_by(AIProviderAttempt.status))],
        "cost_24h": {status: str(amount or ZERO) for status, amount in db.execute(select(
            AIProviderAttempt.status, func.sum(AIProviderAttempt.estimated_usd),
        ).where(AIProviderAttempt.started_at >= moment - timedelta(days=1)).group_by(AIProviderAttempt.status))},
    }
