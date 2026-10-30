"""Local account erasure, serialized with chat persistence and financial settlement."""
from fastapi import HTTPException
from sqlalchemy import delete, select, update

from backend.core import ai_operations as ops
from backend.core.leases import transaction_timeouts
from backend.db.ai_models import AIRateLimit
from backend.db.auth_models import User
from backend.db.idempotency_models import AIIdempotency
from backend.db.operations_models import AIOperation
from backend.db.user_data_models import ChatSession


def delete_account_data(db, user_id, expected_password_hash=None):
    transaction_timeouts(db)
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_active or (expected_password_hash is not None and user.password_hash != expected_password_hash):
        raise HTTPException(401, "Account is no longer available")
    # Create-chat already locks the user row. Existing sends/saves lock the chat
    # first; acquiring every chat before the financial gate preserves that order.
    db.scalars(select(ChatSession.id).where(ChatSession.user_id == user_id)
               .order_by(ChatSession.id).with_for_update()).all()
    ops.gate(db)
    requests = select(AIIdempotency.id).where(AIIdempotency.user_id == user_id)
    operations = db.scalars(select(AIOperation).where(AIOperation.request_id.in_(requests))).all()
    for operation in operations:
        ops.finish(db, operation, "account_deleted")
        operation.request_id = None
    db.flush()
    db.execute(delete(AIRateLimit).where(AIRateLimit.bucket_key.startswith(user_id + ":", autoescape=True)))
    db.execute(delete(User).where(User.id == user_id))
    db.commit()
    ops.event("account_deletion_completed", audit_operations_retained=len(operations))
