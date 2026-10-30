import asyncio
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api import ai, auth
from backend.core import rate_limit
from backend.core import leases
from backend.ai import generation
from backend.db.ai_models import AIRateLimit
from backend.db.auth_models import User
from backend.db.database import Base
from backend.db.auth_security_models import AuthRateLimit
from security_client import SecurityClient
from backend.db.database import get_db
from backend.db.idempotency_models import AIIdempotency
from backend.db.lease_models import ChatGenerationLease
from backend.db.user_data_models import Bookmark, ChatMessage, ChatSession
from backend.main import app


class AISendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        for table in (User.__table__, ChatSession.__table__, ChatMessage.__table__, Bookmark.__table__, AIRateLimit.__table__, AIIdempotency.__table__, ChatGenerationLease.__table__, AuthRateLimit.__table__):
            table.create(self.engine)
        Base.metadata.create_all(self.engine)

        def local_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = local_db
        self.now = 1800000000
        self.patches = [
            patch.object(auth.settings, "JWT_SECRET_KEY", "test-ai-send-secret-key-with-sufficient-length"),
            patch.object(auth.settings, "AUTH_COOKIE_SECURE", False),
            patch.object(rate_limit, "time", SimpleNamespace(time=lambda: self.now)),
        ]
        for item in self.patches:
            item.start()
        async def unique_request_key(request):
            if request.url.path.endswith('/send') and 'Idempotency-Key' not in request.headers:
                request.headers['Idempotency-Key'] = str(uuid.uuid4())

        self.clients = [SecurityClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            event_hooks={"request": [unique_request_key]},
        ) for _ in range(3)]
        self.a, self.b, self.guest = self.clients
        self.user_ids = []
        for client, email in ((self.a, "alice@example.com"), (self.b, "bob@example.com")):
            response = await client.post("/api/v1/auth/register", json={"email": email, "password": "Testing password123!"})
            self.assertEqual(response.status_code, 202)
            response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Testing password123!"})
            self.assertEqual(response.status_code, 200)
            self.user_ids.append(response.json()["id"])
        response = await self.a.post("/api/v1/user/chats", json={})
        self.chat_id = response.json()["id"]
        self.path = f"/api/v1/ai/chats/{self.chat_id}/send"
        self.answer = {"answer": "Source answer [2:255]", "sources": [{"type": "quran", "reference": "2:255",
                        "provider": "AlQuran Cloud", "verified": False}], "model": "test"}

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def count_messages(self):
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(ChatMessage))

    def quota(self, index=0):
        with Session(self.engine) as session:
            return session.get(AIRateLimit, f"{self.user_ids[index]}:{self.now // 60}")

    def reservations(self):
        with Session(self.engine) as session:
            return session.scalars(select(AIIdempotency)).all()

    def current_lease(self):
        with Session(self.engine) as session:
            return session.get(ChatGenerationLease, self.chat_id)

    def expire_lease(self):
        with Session(self.engine) as session:
            session.get(ChatGenerationLease, self.chat_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            session.commit()

    def reserve(self, key, message="Question"):
        with Session(self.engine) as session:
            return ai.reserve_turn(session, self.user_ids[0], self.chat_id, key, message)

    def save(self, reserved, message="Question", answer=None):
        with Session(self.engine) as session:
            ai.save_turn(session, self.user_ids[0], self.chat_id, message,
                         ai.ChatResponse.model_validate(answer or self.answer), reserved["lease"])

    def release(self, reserved):
        with Session(self.engine) as session:
            ai.release_failed_request(session, reserved["lease"])

    async def test_server_creates_and_persists_both_messages(self):
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            response = await self.a.post(self.path, json={"message": "  Explain 2:255  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        ask.assert_awaited_once_with(question="Explain 2:255", history=[])
        stored = (await self.a.get(f"/api/v1/user/chats/{self.chat_id}")).json()
        self.assertEqual(stored["title"], "Explain 2:255")
        self.assertEqual([message["role"] for message in stored["messages"]], ["user", "assistant"])
        self.assertEqual(stored["messages"][0]["content"], "Explain 2:255")
        self.assertEqual(stored["messages"][1]["sources"], self.answer["sources"])
        self.assertEqual(self.quota().count, 1)

    async def test_source_metadata_survives_persistence_and_completed_replay(self):
        from backend.ai.retriever import source
        from backend.ai.citation_guard import validate_grounded_answer
        record = source("quran", "1:1", "AlQuran Cloud", translation="Synthetic fixture translation.")
        checked = validate_grounded_answer(json.dumps({"explanation": "Cautious explanation [1:1]", "quotations": []}), [record], "en")
        generated = {"answer": checked["answer"], "sources": checked["sources"], "model": "test"}
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=generated)) as ask:
            sent = await self.a.post(self.path, json={"message": "Source fixture"}, headers=headers)
            replay = await self.a.post(self.path, json={"message": "Source fixture"}, headers=headers)
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.json(), replay.json())
        ask.assert_awaited_once()
        stored = (await self.a.get(f"/api/v1/user/chats/{self.chat_id}")).json()["messages"][-1]
        self.assertEqual(stored["sources"], sent.json()["sources"])
        self.assertEqual(stored["sources"][0]["source_id"], record["source_id"])
        self.assertEqual(stored["sources"][0]["translation_edition"], "en.itani")
        self.assertFalse(stored["sources"][0]["verified"])

    async def test_read_only_recovery_status_requires_ownership_and_never_calls_provider(self):
        key = str(uuid.uuid4())
        reserved = self.reserve(key)
        path = f"/api/v1/ai/chats/{self.chat_id}/requests/{key}"
        with patch.object(ai, "ask_nafsiy", AsyncMock()) as ask:
            self.assertEqual((await self.a.get(path)).json(), {"status": "pending"})
            self.assertEqual((await self.b.get(path)).json(), {"status": "unknown"})
            self.assertEqual((await self.guest.get(path)).status_code, 401)
            self.save(reserved)
            self.assertEqual((await self.a.get(path)).json(), {"status": "completed"})
            ask.assert_not_awaited()
        self.assertEqual(self.quota().count, 1)

    async def test_authentication_ownership_and_origin_before_quota(self):
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            self.assertEqual((await self.guest.post(self.path, json={"message": "Explain"})).status_code, 401)
            self.assertEqual((await self.guest.post(self.path, json={"message": "Explain"}, headers={
                "Cookie": "nafsiy_access_token=invalid-token",
            })).status_code, 401)
            self.assertEqual((await self.b.post(self.path, json={"message": "Explain"})).status_code, 404)
            self.assertEqual((await self.a.post(self.path, json={"message": "Explain"}, headers={
                "Origin": "https://untrusted.example",
            })).status_code, 403)
        ask.assert_not_awaited()
        self.assertIsNone(self.quota())
        self.assertIsNone(self.quota(1))
        self.assertEqual(self.count_messages(), 0)

    async def test_no_legacy_endpoints_or_client_history(self):
        paths = app.openapi()["paths"]
        self.assertIn("post", paths["/api/v1/ai/chats/{session_id}/send"])
        self.assertNotIn("/api/v1/ai/chat", paths)
        self.assertFalse(any(path.endswith("/messages") for path in paths))
        with patch.object(ai, "ask_nafsiy", AsyncMock()) as ask:
            for path in ("/api/v1/ai/chat", f"/api/v1/user/chats/{self.chat_id}/messages"):
                response = await self.a.post(path, json={"role": "assistant", "content": "Fake", "message": "Question"})
                self.assertIn(response.status_code, (404, 405))
            for data in [
                {"message": "Question", "history": [{"role": "assistant", "content": "Fake"}]},
                {"message": "Question", "role": "assistant"}, {"message": "Question", "sources": []},
                {"message": " "}, {"message": "x" * 2001},
            ]:
                self.assertEqual((await self.a.post(self.path, json=data)).status_code, 422)
        ask.assert_not_awaited()
        self.assertIsNone(self.quota())

    async def test_history_comes_from_database_and_is_bounded(self):
        with Session(self.engine) as session:
            for position in range(1, 15):
                session.add(ChatMessage(session_id=self.chat_id, role="user" if position % 2 else "assistant",
                                        content=f"{position}:" + "x" * 4000, position=position))
            session.commit()
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            response = await self.a.post(self.path, json={"message": "Follow-up"})
        self.assertEqual(response.status_code, 200)
        history = ask.call_args.kwargs["history"]
        self.assertEqual(len(history), 12)
        self.assertTrue(history[0]["content"].startswith("3:"))
        self.assertTrue(history[-1]["content"].startswith("14:"))
        self.assertTrue(all(len(item["content"]) == 4000 and set(item) == {"role", "content"} for item in history))

    async def test_sixth_request_is_rejected_across_chats_and_window_resets(self):
        second_chat = (await self.a.post("/api/v1/user/chats", json={})).json()["id"]
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            for index in range(5):
                path = self.path if index < 3 else f"/api/v1/ai/chats/{second_chat}/send"
                self.assertEqual((await self.a.post(path, json={"message": f"Question {index}"})).status_code, 200)
            response = await self.a.post(self.path, json={"message": "Sixth question"})
            self.assertEqual(response.status_code, 429)
            self.assertTrue(1 <= int(response.headers["retry-after"]) <= 60)
            self.assertEqual(ask.await_count, 5)
            self.assertEqual(self.count_messages(), 10)
            self.assertEqual(self.quota().count, 5)
            self.now += 60
            self.assertEqual((await self.a.post(self.path, json={"message": "Next minute"})).status_code, 200)
        self.assertEqual(self.quota().count, 1)

    async def test_failed_ai_requests_consume_quota_without_messages(self):
        with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=HTTPException(502, "Provider failed"))) as ask:
            for _ in range(5):
                self.assertEqual((await self.a.post(self.path, json={"message": "Question"})).status_code, 502)
            self.assertEqual((await self.a.post(self.path, json={"message": "Sixth"})).status_code, 429)
            self.assertEqual(ask.await_count, 5)
        self.assertEqual(self.quota().count, 5)
        self.assertEqual(self.count_messages(), 0)

    async def test_database_failure_rolls_back_both_messages_but_keeps_quota(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TRIGGER reject_assistant BEFORE INSERT ON chat_messages "
                                    "WHEN NEW.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'test failure'); END"))
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            response = await self.a.post(self.path, json={"message": "Question"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.count_messages(), 0)
        self.assertEqual(self.quota().count, 1)
        self.assertEqual((await self.a.get(f"/api/v1/user/chats/{self.chat_id}")).json()["title"], "Yangi suhbat")

    async def test_deleted_chat_during_generation_is_not_recreated(self):
        async def generate(**kwargs):
            with Session(self.engine) as session:
                session.delete(session.get(ChatSession, self.chat_id))
                session.commit()
            return self.answer

        with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)):
            response = await self.a.post(self.path, json={"message": "Question"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.count_messages(), 0)
        self.assertEqual(self.quota().count, 1)

    async def test_bad_ai_result_is_not_persisted(self):
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value={**self.answer, "answer": "x" * 20001})):
            response = await self.a.post(self.path, json={"message": "Question"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.count_messages(), 0)
        self.assertEqual(self.quota().count, 1)

    async def test_latest_hundred_messages_remain_in_order(self):
        with Session(self.engine) as session:
            for position in range(1, 101):
                session.add(ChatMessage(session_id=self.chat_id, role="user" if position % 2 else "assistant",
                                        content=str(position), position=position))
            session.commit()
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            self.assertEqual((await self.a.post(self.path, json={"message": "Newest question"})).status_code, 200)
        messages = (await self.a.get(f"/api/v1/user/chats/{self.chat_id}")).json()["messages"]
        self.assertEqual(len(messages), 100)
        self.assertEqual(messages[0]["content"], "3")
        self.assertEqual([item["role"] for item in messages[-2:]], ["user", "assistant"])

    async def test_quota_is_independent_per_user(self):
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            for _ in range(5):
                await self.a.post(self.path, json={"message": "Question"})
            chat_b = (await self.b.post("/api/v1/user/chats", json={})).json()["id"]
            response = await self.b.post(f"/api/v1/ai/chats/{chat_b}/send", json={"message": "Question"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.quota(1).count, 1)

    async def test_replay_returns_saved_response_without_model_or_extra_quota(self):
        key = uuid.uuid4()
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            first = await self.a.post(self.path, json={"message": "  Question  "}, headers={"Idempotency-Key": str(key)})
            replay = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key.hex.upper()})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        ask.assert_awaited_once()
        self.assertEqual(self.count_messages(), 2)
        self.assertEqual(self.quota().count, 1)
        record, = self.reservations()
        self.assertEqual(record.request_key, str(key))
        self.assertEqual(record.status, "completed")
        self.assertEqual(json.loads(record.response_json), self.answer)

    async def test_same_key_with_different_message_is_rejected(self):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            self.assertEqual((await self.a.post(self.path, json={"message": "First"}, headers=headers)).status_code, 200)
            response = await self.a.post(self.path, json={"message": "Different"}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertIn("different message", response.json()["detail"])
        ask.assert_awaited_once()
        self.assertEqual(self.quota().count, 1)
        self.assertEqual(self.count_messages(), 2)

    async def test_missing_or_invalid_key_never_reserves_or_calls_model(self):
        hooks = self.a.event_hooks["request"]
        self.a.event_hooks["request"] = []
        try:
            with patch.object(ai, "ask_nafsiy", AsyncMock()) as ask:
                for headers in ({}, {"Idempotency-Key": "not-a-uuid"}, {"Idempotency-Key": ""}):
                    response = await self.a.post(self.path, json={"message": "Question"}, headers=headers)
                    self.assertEqual(response.status_code, 422)
                ask.assert_not_awaited()
        finally:
            self.a.event_hooks["request"] = hooks
        self.assertEqual(self.reservations(), [])
        self.assertIsNone(self.quota())

    async def test_parallel_same_key_returns_processing_then_replays(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        headers = {"Idempotency-Key": str(uuid.uuid4())}

        async def generate(**kwargs):
            entered.set()
            await finish.wait()
            return self.answer

        with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)) as ask:
            first = asyncio.create_task(self.a.post(self.path, json={"message": "Question"}, headers=headers))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                duplicate = await self.a.post(self.path, json={"message": "Question"}, headers=headers)
                self.assertEqual(duplicate.status_code, 409)
                self.assertEqual(duplicate.json()["detail"], "Request is already processing")
                different = await self.a.post(self.path, json={"message": "Changed"}, headers=headers)
                self.assertEqual(different.status_code, 409)
                self.assertEqual(self.quota().count, 1)
                self.assertEqual(self.count_messages(), 0)
            finally:
                finish.set()
                response = await first
            replay = await self.a.post(self.path, json={"message": "Question"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(replay.json(), response.json())
        ask.assert_awaited_once()
        self.assertEqual(self.count_messages(), 2)

    async def test_completed_replay_is_available_even_when_rate_limit_is_full(self):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            first = await self.a.post(self.path, json={"message": "Question"}, headers=headers)
            for index in range(4):
                await self.a.post(self.path, json={"message": f"Other {index}"})
            blocked_headers = {"Idempotency-Key": str(uuid.uuid4())}
            blocked = await self.a.post(self.path, json={"message": "Blocked"}, headers=blocked_headers)
            self.assertEqual(blocked.status_code, 429)
            replay = await self.a.post(self.path, json={"message": "Question"}, headers=headers)
            self.assertEqual(replay.json(), first.json())
            self.assertEqual(len(self.reservations()), 5)
            self.assertEqual(self.quota().count, 5)
            self.now += 60
            retry = await self.a.post(self.path, json={"message": "Blocked"}, headers=blocked_headers)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(ask.await_count, 6)

    async def test_provider_failure_releases_key_for_retry(self):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=[HTTPException(502, "Provider failed"), self.answer])) as ask:
            self.assertEqual((await self.a.post(self.path, json={"message": "Question"}, headers=headers)).status_code, 502)
            self.assertEqual(self.reservations()[0].status, "processing")
            self.assertEqual(self.count_messages(), 0)
            self.assertEqual((await self.a.post(self.path, json={"message": "Question"}, headers=headers)).status_code, 200)
        self.assertEqual(ask.await_count, 2)
        self.assertEqual(self.quota().count, 1)
        self.assertEqual(self.count_messages(), 2)

    async def test_cancelled_generation_releases_processing_key(self):
        entered = asyncio.Event()

        async def generate(**kwargs):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)):
            request = asyncio.create_task(self.a.post(self.path, json={"message": "Question"}))
            await asyncio.wait_for(entered.wait(), 5)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
        self.assertEqual(self.reservations()[0].status, "processing")
        self.assertEqual(self.count_messages(), 0)
        self.assertEqual(self.quota().count, 1)

    async def test_failure_after_commit_keeps_completed_response_for_retry(self):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        save_turn = ai.save_turn

        def lose_response(*args):
            save_turn(*args)
            raise HTTPException(503, "Response lost after commit")

        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            with patch.object(ai, "save_turn", side_effect=lose_response):
                failed = await self.a.post(self.path, json={"message": "Question"}, headers=headers)
            self.assertEqual(failed.status_code, 503)
            self.assertEqual(self.reservations()[0].status, "completed")
            retry = await self.a.post(self.path, json={"message": "Question"}, headers=headers)
        self.assertEqual(retry.status_code, 200)
        ask.assert_awaited_once()
        self.assertEqual(self.count_messages(), 2)
        self.assertEqual(self.quota().count, 1)

    async def test_completion_failure_rolls_back_messages_and_releases_key(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TRIGGER reject_completion BEFORE UPDATE ON ai_idempotency "
                                    "WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'test failure'); END"))
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            response = await self.a.post(self.path, json={"message": "Question"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.count_messages(), 0)
        self.assertEqual(self.reservations()[0].status, "processing")
        self.assertEqual(self.quota().count, 1)
        self.assertEqual((await self.a.get(f"/api/v1/user/chats/{self.chat_id}")).json()["title"], "Yangi suhbat")

    async def test_same_key_is_scoped_to_user_and_chat(self):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        chat_a = (await self.a.post("/api/v1/user/chats", json={})).json()["id"]
        chat_b = (await self.b.post("/api/v1/user/chats", json={})).json()["id"]
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            for client, path in ((self.a, self.path), (self.a, f"/api/v1/ai/chats/{chat_a}/send"),
                                 (self.b, f"/api/v1/ai/chats/{chat_b}/send")):
                response = await client.post(path, json={"message": "Question"}, headers=headers)
                self.assertEqual(response.status_code, 200)
        self.assertEqual(ask.await_count, 3)
        self.assertEqual(len(self.reservations()), 3)
        self.assertEqual(self.quota().count, 2)
        self.assertEqual(self.quota(1).count, 1)

    async def test_chat_deletion_cascades_saved_response(self):
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            self.assertEqual((await self.a.post(self.path, json={"message": "Question"})).status_code, 200)
        self.assertEqual(len(self.reservations()), 1)
        self.assertEqual((await self.a.delete(f"/api/v1/user/chats/{self.chat_id}")).status_code, 204)
        self.assertEqual(self.reservations(), [])
        self.assertEqual(self.count_messages(), 0)

    async def test_cors_accepts_idempotency_header(self):
        for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
            response = await self.a.options(self.path, headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Idempotency-Key",
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("idempotency-key", response.headers["access-control-allow-headers"].lower())

        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            response = await self.a.post(self.path, json={"message": "Question"}, headers={"Origin": "http://localhost:3000"})
        self.assertIn("Retry-After", response.headers["access-control-expose-headers"])

    async def test_startup_does_not_create_database_tables(self):
        from backend.db.database import Base

        with patch.object(Base.metadata, "create_all") as create:
            async with app.router.lifespan_context(app):
                pass
        create.assert_not_called()

    async def test_migration_upgrade_and_downgrade_preserve_existing_tables(self):
        from importlib import import_module
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        from backend.db.database import Base

        migration = import_module("migrations.versions.7bbcad9d4644_add_ai_idempotency")
        recovery = import_module("migrations.versions.f9a045983bdf_add_generation_leases_and_recovery")
        security = import_module("migrations.versions.ade01cf7b885_add_authentication_rate_limits")
        operations = import_module("migrations.versions.c4d8e2a9106f_add_ai_operations_and_budgets")
        self.assertEqual(migration.down_revision, "69e1d999085f")
        engine = create_engine("sqlite://")
        try:
            existing = [table for table in Base.metadata.sorted_tables if table.name not in ("ai_idempotency", "chat_generation_leases", "auth_rate_limits", "ai_operations_gate", "ai_global_slots", "ai_budgets", "ai_operations", "ai_provider_attempts")]
            Base.metadata.create_all(engine, tables=existing)
            with engine.begin() as connection:
                connection.execute(text("DROP INDEX ix_ai_rate_limits_created_at"))
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration.upgrade()
                    recovery.upgrade()
                    security.upgrade()
                    operations.upgrade()
                    self.assertEqual(compare_metadata(context, Base.metadata), [])
                    operations.downgrade()
                    security.downgrade()
                    recovery.downgrade()
                    migration.downgrade()
                    self.assertNotIn("ai_idempotency", inspect(connection).get_table_names())
                    self.assertEqual(set(inspect(connection).get_table_names()), {table.name for table in existing})
        finally:
            engine.dispose()

    async def test_different_keys_are_busy_without_extra_quota_or_reservation(self):
        entered, finish = asyncio.Event(), asyncio.Event()

        async def generate(**kwargs):
            entered.set()
            await finish.wait()
            return self.answer

        with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)) as ask:
            first = asyncio.create_task(self.a.post(self.path, json={"message": "First question"}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                busy = await self.a.post(self.path, json={"message": "Second question"})
                self.assertEqual(busy.status_code, 409)
                self.assertEqual(busy.json()["detail"], "Chat is busy; retry later")
                self.assertGreaterEqual(int(busy.headers["retry-after"]), 1)
                self.assertEqual(len(self.reservations()), 1)
                self.assertEqual(self.quota().count, 1)
            finally:
                finish.set()
                self.assertEqual((await first).status_code, 200)
            second = await self.a.post(self.path, json={"message": "Second question"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(ask.call_args.kwargs["history"]), 2)
        self.assertEqual(self.count_messages(), 4)

    async def test_completed_replay_works_while_another_key_holds_chat(self):
        key = str(uuid.uuid4())
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            first = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
            active = self.reserve(str(uuid.uuid4()), "Other question")
            replay = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
            self.assertEqual(replay.json(), first.json())
            ask.assert_awaited_once()
            self.assertEqual(self.quota().count, 2)
            self.assertEqual(self.current_lease().owner_id, active["lease"].owner_id)

    async def test_expired_same_key_recovers_once_and_fences_old_save_and_cleanup(self):
        key = str(uuid.uuid4())
        old = self.reserve(key)
        self.expire_lease()
        new = self.reserve(key)
        self.assertGreater(new["lease"].version, old["lease"].version)
        self.assertNotEqual(new["lease"].owner_id, old["lease"].owner_id)
        self.assertEqual(self.quota().count, 1)
        with self.assertRaises(HTTPException) as failure:
            self.save(old)
        self.assertEqual(failure.exception.status_code, 409)
        self.release(old)
        self.assertEqual(self.current_lease().owner_id, new["lease"].owner_id)
        with Session(self.engine) as session:
            leases.renew_lease(session, new["lease"])
        self.save(new)
        with self.assertRaises(HTTPException):
            self.save(old)
        self.release(old)
        replay = self.reserve(key)
        self.assertEqual(replay["state"], "replay")
        self.assertEqual(self.count_messages(), 2)
        self.assertEqual(self.reservations()[0].status, "completed")

    async def test_expired_different_key_cannot_be_overwritten_and_old_key_uses_fresh_history(self):
        old_key, new_key = str(uuid.uuid4()), str(uuid.uuid4())
        old = self.reserve(old_key, "Old question")
        self.expire_lease()
        new = self.reserve(new_key, "New question")
        self.save(new, "New question")
        with self.assertRaises(HTTPException):
            self.save(old, "Old question")
        recovered = self.reserve(old_key, "Old question")
        self.assertEqual(len(recovered["history"]), 2)
        self.assertEqual(recovered["history"][0]["content"], "New question")
        self.assertEqual(self.quota().count, 2)
        self.save(recovered, "Old question")
        self.assertEqual(self.count_messages(), 4)

    async def test_expired_owner_cannot_renew_or_save_even_without_takeover(self):
        old = self.reserve(str(uuid.uuid4()))
        self.expire_lease()
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException):
                leases.renew_lease(session, old["lease"])
        with self.assertRaises(HTTPException):
            self.save(old)
        self.assertEqual(self.count_messages(), 0)

    async def test_heartbeat_keeps_lease_alive_during_generation(self):
        async def generate(**kwargs):
            await asyncio.sleep(0.5)
            return self.answer

        with patch.object(leases, "LEASE_SECONDS", 0.2), patch.object(leases, "RENEW_INTERVAL_SECONDS", 0.03):
            with patch.object(leases, "renew_lease", wraps=leases.renew_lease) as renew:
                with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)):
                    response = await self.a.post(self.path, json={"message": "Question"})
                self.assertEqual(response.status_code, 200)
                self.assertGreater(renew.call_count, 2)
        self.assertEqual(self.count_messages(), 2)

    async def test_heartbeat_failure_cancels_generation_and_retry_keeps_quota(self):
        cancelled = asyncio.Event()
        real_renew = leases.renew_lease
        calls = 0

        def renew(db, lease):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise HTTPException(503, "Test renewal failure")
            real_renew(db, lease)

        async def generate(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        key = str(uuid.uuid4())
        with patch.object(leases, "RENEW_INTERVAL_SECONDS", 0.02), patch.object(leases, "renew_lease", side_effect=renew):
            with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)):
                response = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.count_messages(), 0)
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            retry = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(self.quota().count, 1)

    async def test_generation_timeout_releases_owner_without_recharging_retry(self):
        key = str(uuid.uuid4())
        cancelled = asyncio.Event()

        async def generate(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(generation, "GENERATION_TIMEOUT_SECONDS", 0.03):
            with patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)):
                response = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
        self.assertEqual(response.status_code, 504)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.count_messages(), 0)
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
            self.assertEqual((await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})).status_code, 200)
        self.assertEqual(self.quota().count, 1)

    async def test_cancel_during_reservation_commit_does_not_leak_active_owner(self):
        committed, unblock = threading.Event(), threading.Event()
        original = ai.reserve_turn

        def reserve(*args):
            result = original(*args)
            committed.set()
            unblock.wait(5)
            return result

        with patch.object(ai, "reserve_turn", side_effect=reserve), patch.object(ai, "ask_nafsiy", AsyncMock()) as ask:
            request = asyncio.create_task(self.a.post(self.path, json={"message": "Question"}))
            self.assertTrue(await asyncio.to_thread(committed.wait, 5))
            request.cancel()
            unblock.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
            ask.assert_not_awaited()
        record = self.reservations()[0]
        recovered = self.reserve(record.request_key)
        self.assertEqual(recovered["state"], "reserved")
        self.assertEqual(self.quota().count, 1)

    async def test_cancel_during_successful_commit_keeps_replay(self):
        committed, unblock = threading.Event(), threading.Event()
        original = ai.save_turn
        key = str(uuid.uuid4())

        def save(*args):
            original(*args)
            committed.set()
            unblock.wait(5)

        with patch.object(ai, "save_turn", side_effect=save), patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)) as ask:
            request = asyncio.create_task(self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key}))
            self.assertTrue(await asyncio.to_thread(committed.wait, 5))
            request.cancel()
            unblock.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
            replay = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
            self.assertEqual(replay.status_code, 200)
            ask.assert_awaited_once()
        self.assertEqual(self.count_messages(), 2)
        self.assertEqual(self.quota().count, 1)

    async def test_quota_and_reservation_rollback_together_before_acceptance(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TRIGGER reject_reservation BEFORE UPDATE ON ai_idempotency "
                                    "BEGIN SELECT RAISE(ABORT, 'test failure'); END"))
        with patch.object(ai, "ask_nafsiy", AsyncMock()) as ask:
            response = await self.a.post(self.path, json={"message": "Question"})
            ask.assert_not_awaited()
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.quota())
        self.assertEqual(self.reservations(), [])
        self.assertIsNone(self.current_lease())

    async def test_recovered_key_is_not_recharged_in_a_new_rate_window(self):
        key = str(uuid.uuid4())
        self.reserve(key)
        old_quota = self.quota().count
        self.expire_lease()
        self.now += 60
        recovered = self.reserve(key)
        self.save(recovered)
        self.assertIsNone(self.quota())
        self.assertEqual(old_quota, 1)

    async def test_correct_owner_with_stale_fencing_version_cannot_write(self):
        reserved = self.reserve(str(uuid.uuid4()))
        current = reserved["lease"]
        stale = leases.GenerationLease(current.session_id, current.request_id, current.owner_id, current.version - 1)
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException):
                leases.renew_lease(session, stale)
        with self.assertRaises(HTTPException):
            self.save({"lease": stale})
        self.release({"lease": stale})
        self.save(reserved)
        self.assertEqual(self.count_messages(), 2)

    async def test_heartbeat_detects_takeover_and_cancels_stale_model(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def generate(**kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        key = str(uuid.uuid4())
        with patch.object(leases, "RENEW_INTERVAL_SECONDS", 0.03), patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)):
            first = asyncio.create_task(self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key}))
            await asyncio.wait_for(entered.wait(), 5)
            self.expire_lease()
            newer = self.reserve(key)
            response = await first
        self.assertEqual(response.status_code, 409)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.current_lease().owner_id, newer["lease"].owner_id)
        self.assertEqual(self.count_messages(), 0)
        self.save(newer)
        self.assertEqual(self.count_messages(), 2)
        self.assertEqual(self.quota().count, 1)

    async def test_cancellation_resistant_provider_cannot_save_after_timeout(self):
        release, finished = asyncio.Event(), asyncio.Event()
        key = str(uuid.uuid4())

        async def generate(**kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                finished.set()
                return {**self.answer, "answer": "Late stale answer"}

        try:
            with patch.object(generation, "GENERATION_TIMEOUT_SECONDS", 0.03), \
                    patch.object(generation, "CANCELLATION_GRACE_SECONDS", 0.01), \
                    patch.object(ai, "ask_nafsiy", AsyncMock(side_effect=generate)):
                response = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
            self.assertEqual(response.status_code, 504)
            with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=self.answer)):
                retry = await self.a.post(self.path, json={"message": "Question"}, headers={"Idempotency-Key": key})
            self.assertEqual(retry.status_code, 200)
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 5)
        messages = (await self.a.get(f"/api/v1/user/chats/{self.chat_id}")).json()["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["content"], self.answer["answer"])
        self.assertEqual(self.quota().count, 1)
