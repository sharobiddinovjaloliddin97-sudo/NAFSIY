import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
import uuid
import time
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api import ai
from backend.ai import provider
from backend.core import ai_operations as ops
from backend.core.config import settings
from backend.core.leases import clock
from backend.db.database import Base
from backend.db.auth_models import User
from backend.db.ai_models import AIRateLimit
from backend.db.idempotency_models import AIIdempotency
from backend.db.lease_models import ChatGenerationLease
from backend.db.operations_models import AIBudget, AIGlobalSlot, AIOperation, AIProviderAttempt
from backend.db.user_data_models import ChatMessage, ChatSession


class Fixture:
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.user = uuid.uuid4().hex
        self.chats = [uuid.uuid4().hex for _ in range(3)]
        with Session(self.engine) as db:
            db.add(User(id=self.user, email=f"{self.user}@example.com", password_hash="unusable"))
            db.flush()
            db.add_all([ChatSession(id=chat, user_id=self.user) for chat in self.chats])
            db.commit()
        self.config = patch.multiple(settings, AI_DAILY_BUDGET_USD=Decimal("2"), AI_MONTHLY_BUDGET_USD=Decimal("20"),
                                     AI_INPUT_USD_PER_MILLION=Decimal("1"), AI_OUTPUT_USD_PER_MILLION=Decimal("2"),
                                     AI_MAX_PROVIDER_ATTEMPTS=6, MAX_GLOBAL_AI_GENERATIONS=10)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.engine.dispose()

    def reserve(self, index=0, key=None):
        with Session(self.engine) as db:
            return ai.reserve_turn(db, self.user, self.chats[index], key or str(uuid.uuid4()), "Question")

    def start(self, lease, phase="planner"):
        with Session(self.engine) as db:
            return ops.start_attempt(db, lease, phase, 100, 256)

    def usage(self, lease, attempt, inputs=100, outputs=200):
        with Session(self.engine) as db:
            return ops.record_usage(db, lease, attempt, "success", inputs, outputs)

    def release(self, lease):
        with Session(self.engine) as db:
            ai.release_failed_request(db, lease)

    def expire(self, lease):
        with Session(self.engine) as db:
            db.execute(update(AIGlobalSlot).where(AIGlobalSlot.owner == lease.owner_id).values(expires_at=clock(db)))
            db.execute(update(ChatGenerationLease).where(ChatGenerationLease.session_id == lease.session_id).values(expires_at=clock(db)))
            db.commit()


class AIOperationsTests(Fixture, unittest.TestCase):
    def test_capacity_rejection_rolls_back_quota_and_budget_and_replay_bypasses_both(self):
        key = str(uuid.uuid4())
        first = self.reserve(key=key)
        with patch.object(settings, "MAX_GLOBAL_AI_GENERATIONS", 1):
            with self.assertRaises(HTTPException) as blocked:
                self.reserve(1)
            self.assertEqual(blocked.exception.status_code, 429)
            self.assertEqual(blocked.exception.headers["Retry-After"], "5")
            with Session(self.engine) as db:
                ai.save_turn(db, self.user, self.chats[0], "Question", ai.ChatResponse(answer="Answer", model="test"), first["lease"])
            second = self.reserve(1)
            with patch.object(settings, "AI_DAILY_BUDGET_USD", Decimal(0)):
                self.assertEqual(self.reserve(key=key)["state"], "replay")
            self.release(second["lease"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.sum(AIRateLimit.count))), 2)
            self.assertTrue(all(row.reserved_usd == 0 for row in db.scalars(select(AIBudget))))

    def test_budget_boundary_includes_reservations_and_releases_unused_cost(self):
        estimate = ops.cost(32768, 6144, Decimal(1), Decimal(2))
        with patch.object(settings, "AI_DAILY_BUDGET_USD", estimate):
            first = self.reserve()
            with self.assertRaises(HTTPException) as blocked:
                self.reserve(1)
            self.assertEqual(blocked.exception.detail, "AI spending budget unavailable")
            self.release(first["lease"])
            self.release(self.reserve(1)["lease"])

    def test_monthly_budget_blocks_even_when_daily_available(self):
        with patch.object(settings, "AI_MONTHLY_BUDGET_USD", Decimal(0)), self.assertRaises(HTTPException):
            self.reserve()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AIIdempotency)), 0)

    def test_usage_reconciliation_is_idempotent_and_retained_after_chat_deletion(self):
        lease = self.reserve()["lease"]
        attempt = self.start(lease)
        self.usage(lease, attempt)
        self.usage(lease, attempt)
        self.release(lease)
        with Session(self.engine) as db:
            expected = ops.cost(100, 200, Decimal(1), Decimal(2))
            self.assertTrue(all(row.spent_usd == expected and row.reserved_usd == 0 for row in db.scalars(select(AIBudget))))
            self.assertEqual(db.get(AIIdempotency, lease.request_id).provider_attempts, 1)
            db.delete(db.get(ChatSession, self.chats[0]))
            db.commit()
            self.assertIsNone(db.get(AIOperation, lease.owner_id).request_id)
            self.assertEqual(db.get(AIProviderAttempt, attempt).estimated_usd, expected)

    def test_missing_usage_charges_conservative_reserved_attempt_once(self):
        lease = self.reserve()["lease"]
        attempt = self.start(lease)
        self.usage(lease, attempt, None, None)
        self.release(lease)
        with Session(self.engine) as db:
            row = db.get(AIProviderAttempt, attempt)
            self.assertEqual(row.status, "success_estimated")
            self.assertTrue(all(b.spent_usd == row.reserved_usd and b.reserved_usd == 0 for b in db.scalars(select(AIBudget))))

    def test_crash_expiry_conservatively_settles_inflight_and_fences_stale_completion(self):
        key = str(uuid.uuid4())
        old = self.reserve(key=key)["lease"]
        attempt = self.start(old)
        self.expire(old)
        current = self.reserve(key=key)["lease"]
        with self.assertRaises(HTTPException):
            self.usage(old, attempt)
        self.release(old)
        with Session(self.engine) as db:
            self.assertEqual(db.get(AIOperation, old.owner_id).status, "expired")
            self.assertEqual(db.get(AIOperation, current.owner_id).status, "active")
            self.assertEqual(db.get(AIProviderAttempt, attempt).status, "unknown_estimated")
            self.assertEqual(db.scalar(select(func.sum(AIRateLimit.count))), 1)
        self.release(current)

    def test_actual_provider_attempt_limit_survives_recovery(self):
        key = str(uuid.uuid4())
        with patch.object(settings, "AI_MAX_PROVIDER_ATTEMPTS", 1):
            lease = self.reserve(key=key)["lease"]
            self.start(lease)
            self.release(lease)
            with self.assertRaises(HTTPException) as blocked:
                self.reserve(key=key)
            self.assertEqual(blocked.exception.status_code, 429)
        with Session(self.engine) as db:
            self.assertEqual(db.get(AIIdempotency, lease.request_id).provider_attempts, 1)

    def test_no_transaction_during_provider_and_oversize_input_never_starts_attempt(self):
        lease = self.reserve()["lease"]
        with Session(self.engine) as db:
            with self.assertRaises(HTTPException):
                ops.start_attempt(db, lease, "planner", settings.AI_MAX_INPUT_TOKENS + 1, 256)
            db.rollback()
            self.assertEqual(db.get(AIIdempotency, lease.request_id).provider_attempts, 0)
        self.release(lease)

    def test_cleanup_preserves_active_work_and_retires_old_keys_without_reuse(self):
        key = str(uuid.uuid4())
        lease = self.reserve(key=key)["lease"]
        old = datetime.now(timezone.utc) - timedelta(days=31)
        with Session(self.engine) as db:
            db.get(AIIdempotency, lease.request_id).updated_at = old
            db.add(AIRateLimit(bucket_key="fixture:1", count=1, created_at=old))
            current = int(datetime.now(timezone.utc).timestamp()) // 60
            db.add(AIRateLimit(bucket_key=f"recent:{current}", count=1, created_at=old))
            db.commit()
            ops.maintain(db)
            self.assertEqual(db.get(AIIdempotency, lease.request_id).status, "processing")
            self.assertIsNone(db.get(AIRateLimit, "fixture:1"))
            self.assertIsNotNone(db.get(AIRateLimit, f"recent:{current}"))
        self.release(lease)
        with Session(self.engine) as db:
            ops.maintain(db)
        with self.assertRaises(HTTPException) as expired:
            self.reserve(key=key)
        self.assertEqual(expired.exception.status_code, 410)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AIIdempotency)), 1)
            self.assertEqual(db.get(AIIdempotency, lease.request_id).response_json, '{"expired": true}')

    def test_estimate_overrun_is_accounted_even_above_configured_budget(self):
        lease = self.reserve()["lease"]
        attempt = self.start(lease)
        self.usage(lease, attempt, 10000000, 10000000)
        self.release(lease)
        with self.assertRaises(HTTPException):
            self.reserve(1)

    def test_metrics_and_structured_logs_do_not_contain_question_or_credentials(self):
        with self.assertLogs(ops.logger, level="INFO") as captured:
            lease = self.reserve()["lease"]
            self.release(lease)
        text = " ".join(captured.output)
        self.assertNotIn("Question", text)
        self.assertNotIn(self.user, text)
        with Session(self.engine) as db:
            state = ops.snapshot(db)
            self.assertEqual(state["active_generations"], 0)
            self.assertEqual(len(state["budgets"]), 2)

    def test_partial_usage_and_fractional_prices_never_make_reservations_negative(self):
        with patch.multiple(settings, AI_INPUT_USD_PER_MILLION=Decimal("0.1234567891"),
                            AI_OUTPUT_USD_PER_MILLION=Decimal("0.9876543219")):
            lease = self.reserve()["lease"]
            first = self.start(lease)
            self.usage(lease, first, 12, None)
            second = self.start(lease, "answer")
            self.usage(lease, second, 1, 1)
            self.release(lease)
        with Session(self.engine) as db:
            self.assertEqual(db.get(AIProviderAttempt, first).input_tokens, 12)
            self.assertIsNone(db.get(AIProviderAttempt, first).output_tokens)
            self.assertTrue(all(row.reserved_usd == 0 for row in db.scalars(select(AIBudget))))

    def test_capacity_heartbeat_is_fenced_without_waiting_for_budget_gate(self):
        lease = self.reserve()["lease"]
        with Session(self.engine) as db, patch.object(ops, "gate", side_effect=AssertionError("Heartbeat must not queue behind budgets")):
            ops.renew(db, lease)
            db.commit()
            db.execute(update(AIGlobalSlot).where(AIGlobalSlot.owner == lease.owner_id).values(expires_at=clock(db)))
            db.commit()
            with self.assertRaises(HTTPException):
                ops.renew(db, lease)

    def test_slow_admission_rebases_leases_before_any_provider_work(self):
        original = ai.check_ai_rate_limit
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            time.sleep(0.55)
            return result
        with patch("backend.core.leases.LEASE_SECONDS", 0.5), patch.object(ai, "check_ai_rate_limit", side_effect=delayed):
            lease = self.reserve()["lease"]
        with Session(self.engine) as db:
            self.assertTrue(db.scalar(select(ChatGenerationLease.expires_at > clock(db)).where(ChatGenerationLease.session_id == lease.session_id)))
            self.assertTrue(db.scalar(select(AIGlobalSlot.expires_at > clock(db)).where(AIGlobalSlot.owner == lease.owner_id)))
            self.assertEqual(db.scalar(select(func.count()).select_from(AIProviderAttempt)), 0)

    def test_gate_lock_is_reacquired_after_transaction_and_savepoint_rollback(self):
        with Session(self.engine) as db, patch.object(ops, "transaction_timeouts", wraps=ops.transaction_timeouts) as configure:
            ops.gate(db)
            calls = configure.call_count
            ops.gate(db)
            self.assertEqual(configure.call_count, calls)
            db.commit()
            ops.gate(db)
            self.assertGreater(configure.call_count, calls)
            nested = db.begin_nested()
            ops.gate(db)
            calls = configure.call_count
            nested.rollback()
            ops.gate(db)
            self.assertGreater(configure.call_count, calls)


class AIProviderTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def test_network_call_has_no_open_database_transaction(self):
        lease = self.reserve()["lease"]
        token = provider.provider_context.set((self.engine, lease))
        async def network(**kwargs):
            connection = self.engine.raw_connection()
            try:
                self.assertFalse(connection.driver_connection.in_transaction)
            finally:
                connection.close()
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=network)))
        try:
            await provider.completion(client, "planner", model=settings.GROQ_MODEL,
                                      messages=[{"role": "user", "content": "Question"}], max_completion_tokens=256)
        finally:
            provider.provider_context.reset(token)
            self.release(lease)

    async def test_provider_usage_success_missing_metadata_failure_and_timeout(self):
        for failure in (None, "missing", TimeoutError(), RuntimeError("private provider body")):
            with self.subTest(kind=type(failure).__name__):
                lease = self.reserve()["lease"]
                context = provider.provider_context.set((self.engine, lease))
                response = SimpleNamespace(usage=None if failure == "missing" else SimpleNamespace(prompt_tokens=12, completion_tokens=9))
                create = AsyncMock(side_effect=failure if isinstance(failure, BaseException) else None, return_value=response)
                client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
                try:
                    if isinstance(failure, BaseException):
                        with self.assertRaises(type(failure)):
                            await provider.completion(client, "planner", model=settings.GROQ_MODEL,
                                                      messages=[{"role": "user", "content": "Private test question"}], max_completion_tokens=256)
                    else:
                        await provider.completion(client, "planner", model=settings.GROQ_MODEL,
                                                  messages=[{"role": "user", "content": "Private test question"}], max_completion_tokens=256)
                finally:
                    provider.provider_context.reset(context)
                    self.release(lease)
                with Session(self.engine) as db:
                    self.assertFalse(db.scalar(select(func.count()).select_from(AIProviderAttempt).where(AIProviderAttempt.status == "started")))
                    self.assertEqual(db.get(AIIdempotency, lease.request_id).provider_attempts, 1)

    async def test_input_guard_blocks_provider_before_network_call(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())))
        with self.assertRaises(HTTPException):
            await provider.completion(client, "planner", messages=[{"role": "user", "content": "x" * 20000}], max_completion_tokens=256)
        client.chat.completions.create.assert_not_awaited()
