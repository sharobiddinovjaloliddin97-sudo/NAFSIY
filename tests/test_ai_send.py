import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api import ai, auth
from backend.core import rate_limit
from backend.db.ai_models import AIRateLimit
from backend.db.auth_models import User
from backend.db.database import get_db
from backend.db.user_data_models import Bookmark, ChatMessage, ChatSession
from backend.main import app


class AISendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        for table in (User.__table__, ChatSession.__table__, ChatMessage.__table__, Bookmark.__table__, AIRateLimit.__table__):
            table.create(self.engine)

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
        self.clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") for _ in range(3)]
        self.a, self.b, self.guest = self.clients
        self.user_ids = []
        for client, email in ((self.a, "alice@example.com"), (self.b, "bob@example.com")):
            response = await client.post("/api/v1/auth/register", json={"email": email, "password": "Testing password123!"})
            self.assertEqual(response.status_code, 201)
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
