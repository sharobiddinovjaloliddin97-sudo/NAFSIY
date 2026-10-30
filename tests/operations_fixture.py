from sqlalchemy import delete, select

from backend.core import ai_operations as ops
from backend.db.idempotency_models import AIIdempotency
from backend.db.operations_models import AIOperation, AIProviderAttempt


def cleanup_operations(db, user_id):
    ops.gate(db)
    operations = db.scalars(select(AIOperation).join(AIIdempotency, AIOperation.request_id == AIIdempotency.id).where(
        AIIdempotency.user_id == user_id,
    )).all()
    for operation in operations:
        ops.finish(db, operation, "fixture_cleanup")
        charged = sum((attempt.estimated_usd or ops.ZERO) for attempt in db.scalars(select(AIProviderAttempt).where(
            AIProviderAttempt.operation_id == operation.id,
        )))
        for row in ops.budget_rows(db, operation):
            row.spent_usd -= charged
        db.execute(delete(AIProviderAttempt).where(AIProviderAttempt.operation_id == operation.id))
        db.delete(operation)
    db.flush()
