from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from decimal import Decimal
from datetime import timedelta
import multiprocessing
import os
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import Session

from backend.api import ai
from backend.core import ai_operations as ops, leases
from backend.core.config import settings
from backend.db.ai_models import AIRateLimit
from backend.db.auth_models import User
from backend.db.idempotency_models import AIIdempotency
from backend.db.lease_models import ChatGenerationLease
from backend.db.operations_models import AIBudget, AIGlobalSlot, AIOperation, AIProviderAttempt
from backend.db.user_data_models import ChatSession
from operations_fixture import cleanup_operations
from test_idempotency_postgres import _test_database_url


def contend(arguments):
    user, chat, options = arguments
    engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 10}, pool_timeout=5)
    try:
        with patch.multiple(settings, **options), patch.object(leases, "LEASE_SECONDS", 120), Session(engine) as db:
            try:
                reserved = ai.reserve_turn(db, user, chat, str(uuid.uuid4()), "Budget concurrency fixture")
                return {"status": 200, "lease": asdict(reserved["lease"])}
            except HTTPException as error:
                return {"status": error.status_code, "detail": error.detail, "retry_after": (error.headers or {}).get("Retry-After")}
    finally:
        engine.dispose()


def crash_after_provider_marker(user, chat, key, sender):
    engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 10})
    with Session(engine) as db:
        lease = ai.reserve_turn(db, user, chat, key, "Question")["lease"]
        attempt = ops.start_attempt(db, lease, "planner", 100, 256)
        db.execute(update(AIGlobalSlot).where(AIGlobalSlot.owner == lease.owner_id).values(expires_at=leases.clock(db) + timedelta(seconds=1)))
        db.execute(update(ChatGenerationLease).where(ChatGenerationLease.session_id == chat).values(expires_at=leases.clock(db) + timedelta(seconds=1)))
        db.commit()
    sender.send((asdict(lease), attempt))
    sender.close()
    os._exit(31)


def stale_complete(arguments):
    lease, attempt = arguments
    engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 10})
    try:
        with Session(engine) as db:
            try:
                ops.record_usage(db, leases.GenerationLease(**lease), attempt, "success", 12, 9)
                return 200
            except HTTPException as error:
                return error.status_code
    finally:
        engine.dispose()


@unittest.skipUnless(os.getenv("NAFSIY_RUN_POSTGRES_TESTS") == "1", "Explicit PostgreSQL test opt-in required")
class PostgreSQLOperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 10})
        self.user = uuid.uuid4().hex
        self.chats = [uuid.uuid4().hex for _ in range(8)]
        with Session(self.engine) as db:
            db.add(User(id=self.user, email=f"operations-{self.user}@example.com", password_hash="unusable"))
            db.flush()
            db.add_all([ChatSession(id=chat, user_id=self.user) for chat in self.chats])
            db.commit()

    def tearDown(self):
        try:
            with Session(self.engine) as db:
                cleanup_operations(db, self.user)
                db.execute(delete(User).where(User.id == self.user))
                db.execute(delete(AIRateLimit).where(AIRateLimit.bucket_key.like(f"{self.user}:%")))
                db.commit()
        finally:
            self.engine.dispose()

    def race(self, **options):
        with ProcessPoolExecutor(max_workers=4) as pool:
            return list(pool.map(contend, [(self.user, chat, options) for chat in self.chats]))

    def assert_one_budget_winner(self, results):
        self.assertEqual(sum(row["status"] == 200 for row in results), 1, results)
        self.assertTrue(all(row["status"] in (200, 429) for row in results), results)
        for row in results:
            if row["status"] == 429:
                self.assertEqual(row["detail"], "AI spending budget unavailable")
                self.assertGreater(int(row["retry_after"]), 0)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AIIdempotency).where(AIIdempotency.user_id == self.user)), 1)
            self.assertEqual(db.scalar(select(func.sum(AIRateLimit.count)).where(AIRateLimit.bucket_key.like(f"{self.user}:%"))), 1)

    def test_global_capacity_admission_across_four_processes(self):
        results = self.race(MAX_GLOBAL_AI_GENERATIONS=2)
        self.assertEqual(sum(row["status"] == 200 for row in results), 2, results)
        self.assertTrue(all(row["status"] in (200, 429) for row in results), results)
        with Session(self.engine) as db:
            active = db.scalar(select(func.count()).select_from(AIGlobalSlot).where(AIGlobalSlot.expires_at > leases.clock(db)))
            self.assertEqual(active, 2)
            self.assertEqual(db.scalar(select(func.sum(AIRateLimit.count)).where(AIRateLimit.bucket_key.like(f"{self.user}:%"))), 2)

    def test_daily_budget_boundary_race_across_four_processes(self):
        self.assert_one_budget_winner(self.race(AI_DAILY_BUDGET_USD=Decimal("0.06")))

    def test_monthly_budget_boundary_race_across_four_processes(self):
        self.assert_one_budget_winner(self.race(AI_MONTHLY_BUDGET_USD=Decimal("0.06")))

    def test_crashed_attempt_recovery_and_stale_process_cannot_complete_newer_attempt(self):
        key = str(uuid.uuid4())
        receiver, sender = multiprocessing.Pipe(duplex=False)
        worker = multiprocessing.Process(target=crash_after_provider_marker, args=(self.user, self.chats[0], key, sender))
        worker.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(25))
            old, attempt = receiver.recv()
            worker.join(10)
            self.assertEqual(worker.exitcode, 31)
        finally:
            receiver.close()
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
        deadline = time.monotonic() + 12
        while True:
            with Session(self.engine) as db:
                expired = db.scalar(select(AIGlobalSlot.expires_at <= leases.clock(db)).where(AIGlobalSlot.owner == old["owner_id"]))
            if expired:
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        with Session(self.engine) as db:
            lease = ai.reserve_turn(db, self.user, self.chats[0], key, "Question")["lease"]
            self.assertEqual(db.get(AIProviderAttempt, attempt).status, "unknown_estimated")
            self.assertEqual(db.get(AIIdempotency, lease.request_id).provider_attempts, 1)
        with ProcessPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(stale_complete, [(old, attempt)] * 4)), [409] * 4)
        with Session(self.engine) as db:
            current_attempt = ops.start_attempt(db, lease, "planner", 100, 256)
            ops.record_usage(db, lease, current_attempt, "success", 100, 100)
            ops.record_usage(db, lease, current_attempt, "success", 100, 100)
            ai.save_turn(db, self.user, self.chats[0], "Question", ai.ChatResponse(answer="Recovered answer", model="test"), lease)
            with patch.object(settings, "AI_DAILY_BUDGET_USD", Decimal(0)):
                self.assertEqual(ai.reserve_turn(db, self.user, self.chats[0], key, "Question")["state"], "replay")
            self.assertEqual(db.get(AIIdempotency, lease.request_id).provider_attempts, 2)
            self.assertEqual(db.get(AIOperation, old["owner_id"]).status, "expired")

    def test_maintenance_does_not_retire_live_reservations(self):
        with Session(self.engine) as db:
            lease = ai.reserve_turn(db, self.user, self.chats[0], str(uuid.uuid4()), "Question")["lease"]
            ops.start_attempt(db, lease, "planner", 100, 256)
            ops.maintain(db)
            self.assertEqual(db.get(AIOperation, lease.owner_id).status, "active")
            self.assertTrue(all(row.reserved_usd > 0 for row in db.scalars(select(AIBudget))))
