from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import timedelta
import multiprocessing
import os
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.orm import Session

from backend.api import ai
from backend.core import leases
from backend.db.ai_models import AIRateLimit
from backend.db.auth_models import User
from backend.db.idempotency_models import AIIdempotency
from backend.db.lease_models import ChatGenerationLease
from backend.db.user_data_models import ChatMessage, ChatSession
from test_idempotency_postgres import _test_database_url, send_from_process
from security_client import cleanup_postgres_bootstrap


def contend_for_lease(arguments):
    session_id, request_id = arguments
    engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 10}, pool_timeout=5)
    try:
        with Session(engine) as db:
            try:
                leases.transaction_timeouts(db)
                lease = leases.acquire_lease(db, session_id, request_id, same_request=False)
                db.commit()
                return {"status": 200, "lease": asdict(lease)}
            except HTTPException as error:
                db.rollback()
                return {"status": error.status_code, "retry_after": error.headers.get("Retry-After")}
    finally:
        engine.dispose()


def crash_worker(user_id, session_id, key, pipe, complete):
    engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 10})
    leases.LEASE_SECONDS = 30 if complete else 1
    with Session(engine) as db:
        reserved = ai.reserve_turn(db, user_id, session_id, key, "Crash question")
        if complete:
            ai.save_turn(db, user_id, session_id, "Crash question", ai.ChatResponse(
                answer="Committed before crash", sources=[], model="test",
            ), reserved["lease"])
    pipe.send(asdict(reserved["lease"]))
    pipe.close()
    os._exit(29)


def pause_final_commit(user_id, session_id, lease, entered, finish):
    engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 10})
    original = ai.complete_request

    def pause(db, token, response):
        original(db, token, response)
        entered.set()
        if not finish.wait(15):
            raise RuntimeError("Final commit checkpoint timeout")

    try:
        with patch.object(leases, "LEASE_SECONDS", 1), patch.object(ai, "complete_request", side_effect=pause):
            with Session(engine) as db:
                ai.save_turn(db, user_id, session_id, "Question", ai.ChatResponse(
                    answer="Atomic committed answer", sources=[], model="test",
                ), leases.GenerationLease(**lease))
    finally:
        engine.dispose()


@unittest.skipUnless(os.getenv("NAFSIY_RUN_POSTGRES_TESTS") == "1", "Explicit PostgreSQL test opt-in required")
class PostgreSQLGenerationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(_test_database_url(), pool_timeout=5, connect_args={"connect_timeout": 10})
        self.user_id, self.chat_id = uuid.uuid4().hex, uuid.uuid4().hex
        with Session(self.engine) as db:
            db.add(User(id=self.user_id, email=f"lease-{self.user_id}@example.com", password_hash="unusable-test-hash"))
            db.flush()
            db.add(ChatSession(id=self.chat_id, user_id=self.user_id, title="Lease integration test"))
            db.commit()

    def tearDown(self):
        try:
            with Session(self.engine) as db:
                db.execute(delete(User).where(User.id == self.user_id))
                db.execute(delete(AIRateLimit).where(AIRateLimit.bucket_key.like(f"{self.user_id}:%")))
                cleanup_postgres_bootstrap(db)
                db.commit()
                self.assertIsNone(db.get(ChatGenerationLease, self.chat_id))
                self.assertEqual(db.scalar(select(func.count()).select_from(AIIdempotency).where(
                    AIIdempotency.user_id == self.user_id,
                )), 0)
        finally:
            self.engine.dispose()

    def reserve(self, key, message="Question"):
        with Session(self.engine) as db:
            return ai.reserve_turn(db, self.user_id, self.chat_id, key, message)

    def save(self, lease, message="Question"):
        with Session(self.engine) as db:
            ai.save_turn(db, self.user_id, self.chat_id, message, ai.ChatResponse(
                answer="Current owner answer", sources=[], model="test",
            ), lease)

    def expire(self):
        with Session(self.engine) as db:
            db.execute(update(ChatGenerationLease).where(ChatGenerationLease.session_id == self.chat_id).values(
                expires_at=leases.clock(db) - timedelta(seconds=1),
            ))
            db.commit()

    def quota(self):
        with Session(self.engine) as db:
            return db.scalar(select(func.sum(AIRateLimit.count)).where(AIRateLimit.bucket_key.like(f"{self.user_id}:%")))

    def send(self, key, message="Question", session_id=None):
        return send_from_process((self.user_id, session_id or self.chat_id, key, message))

    def crash(self, key, *, complete=False):
        receiver, sender = multiprocessing.Pipe(duplex=False)
        worker = multiprocessing.Process(target=crash_worker, args=(self.user_id, self.chat_id, key, sender, complete))
        worker.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(20), "Crash worker did not reach committed checkpoint")
            lease = leases.GenerationLease(**receiver.recv())
            worker.join(10)
            self.assertEqual(worker.exitcode, 29)
            return lease
        finally:
            receiver.close()
            if worker.is_alive():
                worker.terminate()
                worker.join(5)

    def test_conditional_upsert_has_one_owner_across_four_processes(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(contend_for_lease, [(self.chat_id, uuid.uuid4().hex) for _ in range(8)]))
        self.assertEqual(sum(result["status"] == 200 for result in results), 1)
        self.assertTrue(all(result["status"] in (200, 409) for result in results))
        self.assertTrue(all(int(result["retry_after"]) >= 1 for result in results if result["status"] == 409))
        self.assertIsNone(self.quota())
        with Session(self.engine) as db:
            self.assertEqual(db.get(ChatGenerationLease, self.chat_id).version, 1)

    def test_crashed_processing_recovers_same_key_without_double_charge(self):
        key = str(uuid.uuid4())
        old = self.crash(key)
        deadline = time.monotonic() + 5
        while True:
            with Session(self.engine) as db:
                expired = db.scalar(select(ChatGenerationLease.expires_at <= leases.clock(db)).where(
                    ChatGenerationLease.session_id == self.chat_id,
                ))
            if expired:
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        result = self.send(key, "Crash question")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(self.quota(), 1)
        with self.assertRaises(HTTPException) as error:
            self.save(old, "Crash question")
        self.assertEqual(error.exception.status_code, 409)
        with Session(self.engine) as db:
            current = db.get(ChatGenerationLease, self.chat_id)
            self.assertGreater(current.version, old.version)
            self.assertEqual(db.scalar(select(func.count()).select_from(ChatMessage).where(
                ChatMessage.session_id == self.chat_id,
            )), 2)
        self.assertEqual(self.send(key, "Crash question")["model_calls"], 0)

    def test_crash_after_successful_commit_replays_without_new_generation(self):
        key = str(uuid.uuid4())
        self.crash(key, complete=True)
        replay = self.send(key, "Crash question")
        self.assertEqual(replay["status"], 200)
        self.assertEqual(replay["model_calls"], 0)
        self.assertEqual(replay["body"]["answer"], "Committed before crash")
        self.assertEqual(self.quota(), 1)

    def test_stale_completion_and_cleanup_cannot_modify_new_owner(self):
        key = str(uuid.uuid4())
        old = self.reserve(key)["lease"]
        self.expire()
        current = self.reserve(key)["lease"]
        self.assertGreater(current.version, old.version)
        with self.assertRaises(HTTPException):
            self.save(old)
        with Session(self.engine) as db:
            ai.release_failed_request(db, old)
            leases.renew_lease(db, current)
        self.save(current)
        with self.assertRaises(HTTPException):
            self.save(old)
        self.assertEqual(self.quota(), 1)
        self.assertEqual(self.send(key)["model_calls"], 0)

    def test_expired_owner_cannot_renew_or_save_without_takeover(self):
        old = self.reserve(str(uuid.uuid4()))["lease"]
        self.expire()
        with Session(self.engine) as db:
            with self.assertRaises(HTTPException):
                leases.renew_lease(db, old)
        with self.assertRaises(HTTPException):
            self.save(old)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ChatMessage).where(
                ChatMessage.session_id == self.chat_id,
            )), 0)

    def test_completed_replay_works_while_chat_busy_with_other_key(self):
        key = str(uuid.uuid4())
        self.save(self.reserve(key)["lease"])
        self.reserve(str(uuid.uuid4()), "Other")
        replay = self.send(key)
        busy = self.send(str(uuid.uuid4()), "Third")
        self.assertEqual(replay["status"], 200)
        self.assertEqual(replay["model_calls"], 0)
        self.assertEqual(busy["status"], 409)
        self.assertEqual(busy["model_calls"], 0)
        self.assertGreaterEqual(int(busy["retry_after"]), 1)
        self.assertEqual(self.quota(), 2)

    def test_renewal_holds_owner_without_open_transaction_and_other_chat_proceeds(self):
        second_chat = uuid.uuid4().hex
        with Session(self.engine) as db:
            db.add(ChatSession(id=second_chat, user_id=self.user_id, title="Independent chat"))
            db.commit()
        with multiprocessing.Manager() as manager, ProcessPoolExecutor(max_workers=1) as pool:
            entered, finish = manager.Event(), manager.Event()
            future = pool.submit(send_from_process, (self.user_id, self.chat_id, str(uuid.uuid4()), "Held question", {
                "entered": entered, "finish": finish, "lease_seconds": 3, "renew_interval": 0.15,
            }))
            try:
                self.assertTrue(entered.wait(20))
                with Session(self.engine) as db:
                    generation_checkpoint = db.scalar(select(func.clock_timestamp()))
                time.sleep(3.2)
                with Session(self.engine) as db:
                    active = db.scalar(select(ChatGenerationLease.expires_at > leases.clock(db)).where(
                        ChatGenerationLease.session_id == self.chat_id,
                    ))
                    self.assertTrue(active)
                    open_transactions = db.scalar(text(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name=:name "
                        "AND xact_start < :checkpoint"
                    ), {"name": f"nafsiy-test-{self.user_id}", "checkpoint": generation_checkpoint})
                    self.assertEqual(open_transactions, 0)
                other = self.send(str(uuid.uuid4()), "Independent question", session_id=second_chat)
                self.assertEqual(other["status"], 200)
                busy = self.send(str(uuid.uuid4()), "Busy question")
                self.assertEqual(busy["status"], 409)
                self.assertEqual(busy["model_calls"], 0)
            finally:
                finish.set()
            result = future.result(timeout=20)
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["model_calls"], 1)
            self.assertEqual(self.quota(), 2)

    def test_completion_and_takeover_serialize_even_when_write_crosses_expiry(self):
        key = str(uuid.uuid4())
        token = self.reserve(key)["lease"]
        with multiprocessing.Manager() as manager, ProcessPoolExecutor(max_workers=2) as pool:
            entered, finish = manager.Event(), manager.Event()
            writer = pool.submit(pause_final_commit, self.user_id, self.chat_id, asdict(token), entered, finish)
            try:
                self.assertTrue(entered.wait(20))
                time.sleep(1.2)
                contender = pool.submit(send_from_process, (self.user_id, self.chat_id, key, "Question"))
                deadline = time.monotonic() + 6
                while True:
                    with Session(self.engine) as db:
                        waiting = db.scalar(text(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name=:name AND wait_event_type='Lock'"
                        ), {"name": f"nafsiy-test-{self.user_id}"})
                    if waiting:
                        break
                    self.assertLess(time.monotonic(), deadline, "Contender did not reach final transaction lock")
                    time.sleep(0.02)
            finally:
                finish.set()
            writer.result(timeout=20)
            result = contender.result(timeout=20)
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["body"]["answer"], "Atomic committed answer")
            self.assertEqual(self.quota(), 1)
            with Session(self.engine) as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(ChatMessage).where(
                    ChatMessage.session_id == self.chat_id,
                )), 2)
