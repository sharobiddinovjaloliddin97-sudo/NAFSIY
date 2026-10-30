import json
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api import auth, user_data
from backend.db.auth_models import User
from backend.db.database import get_db
from backend.db.user_data_models import Bookmark, ChatMessage, ChatSession
from backend.main import app


class UserDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        for table in (User.__table__, ChatSession.__table__, ChatMessage.__table__, Bookmark.__table__):
            table.create(self.engine)

        def local_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = local_db
        self.secret_patch = patch.object(auth.settings, "JWT_SECRET_KEY", "test-only-user-data-key-with-sufficient-length")
        self.cookie_patch = patch.object(auth.settings, "AUTH_COOKIE_SECURE", False)
        self.secret_patch.start()
        self.cookie_patch.start()
        self.a = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.b = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.guest = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        for client, email in ((self.a, "alice@example.com"), (self.b, "bob@example.com")):
            response = await client.post("/api/v1/auth/register", json={"email": email, "password": "Testing password123!"})
            self.assertEqual(response.status_code, 201)

    async def asyncTearDown(self):
        for client in (self.a, self.b, self.guest):
            await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        self.secret_patch.stop()
        self.cookie_patch.stop()
        self.engine.dispose()

    async def new_chat(self, client=None):
        response = await (client or self.a).post("/api/v1/user/chats", json={"title": "Yangi suhbat"})
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    async def test_unauthenticated_and_cross_user_access(self):
        chat_id = await self.new_chat()
        bookmark = await self.a.post("/api/v1/user/bookmarks", json={
            "kind": "quran", "reference": "1:1", "title": "Fatiha 1:1",
        })
        bookmark_id = bookmark.json()["id"]
        for method, path, data in [
            ("GET", "/chats", None), ("POST", "/chats", {}),
            ("GET", f"/chats/{chat_id}", None),
            ("DELETE", f"/chats/{chat_id}", None), ("GET", "/bookmarks", None),
            ("POST", "/bookmarks", {"kind": "quran", "reference": "1:2", "title": "Test"}),
            ("DELETE", f"/bookmarks/{bookmark_id}", None),
        ]:
            response = await self.guest.request(method, f"/api/v1/user{path}", json=data)
            self.assertEqual(response.status_code, 401)
        for method, path, data in [
            ("GET", f"/chats/{chat_id}", None),
            ("DELETE", f"/chats/{chat_id}", None), ("DELETE", f"/bookmarks/{bookmark_id}", None),
        ]:
            response = await self.b.request(method, f"/api/v1/user{path}", json=data)
            self.assertEqual(response.status_code, 404)
        self.assertEqual((await self.b.get("/api/v1/user/chats")).json(), [])
        self.assertEqual((await self.b.get("/api/v1/user/bookmarks")).json(), [])
        self.assertEqual((await self.a.get(f"/api/v1/user/chats/{chat_id}")).status_code, 200)

    async def test_stored_messages_roundtrip_and_cascade(self):
        chat_id = await self.new_chat()
        source = {"type": "quran", "reference": "1:1", "provider": "AlQuran Cloud", "verified": False}
        with Session(self.engine) as session:
            session.add_all([
                ChatMessage(session_id=chat_id, role="user", content="First question", position=1, sources_json="[]"),
                ChatMessage(session_id=chat_id, role="assistant", content="Answer", position=2, sources_json=json.dumps([source])),
            ])
            session.commit()
        response = await self.a.get(f"/api/v1/user/chats/{chat_id}")
        self.assertEqual(response.status_code, 200)
        messages = response.json()["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["sources"], [source])
        response = await self.a.get("/api/v1/user/chats")
        self.assertEqual(response.json()[0]["messages"], messages)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.json()[0]["updatedAt"].endswith("Z"))
        self.assertEqual((await self.a.delete(f"/api/v1/user/chats/{chat_id}")).status_code, 204)
        with Session(self.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ChatMessage)), 0)

    async def test_chat_limit_and_validation(self):
        with patch.object(user_data, "MAX_SESSIONS", 2):
            await self.new_chat()
            await self.new_chat()
            response = await self.a.post("/api/v1/user/chats", json={})
            self.assertEqual(response.status_code, 409)
        for title in ("", " ", "x" * 121):
            response = await self.a.post("/api/v1/user/chats", json={"title": title})
            self.assertEqual(response.status_code, 422)

    async def test_bookmark_deduplication_validation_and_cascade(self):
        item = {"kind": "hadith", "reference": "bukhari:1", "title": "Bukhari #1"}
        a = await self.a.post("/api/v1/user/bookmarks", json=item)
        duplicate = await self.a.post("/api/v1/user/bookmarks", json=item)
        b = await self.b.post("/api/v1/user/bookmarks", json=item)
        self.assertEqual(a.json()["id"], duplicate.json()["id"])
        self.assertNotEqual(a.json()["id"], b.json()["id"])
        self.assertEqual(len((await self.a.get("/api/v1/user/bookmarks")).json()), 1)
        for bad in [{**item, "kind": "other"}, {**item, "reference": "../secret:1"},
                    {**item, "reference": "bukhari:0"}, {**item, "reference": "bukhari:100001"},
                    {"kind": "quran", "reference": "115:1", "title": "Test"}]:
            self.assertEqual((await self.a.post("/api/v1/user/bookmarks", json=bad)).status_code, 422)
        self.assertEqual((await self.a.delete(f"/api/v1/user/bookmarks/{a.json()['id']}")).status_code, 204)
        await self.new_chat(self.b)
        with Session(self.engine) as session:
            user = session.scalar(select(User).where(User.email == "bob@example.com"))
            session.delete(user)
            session.commit()
            self.assertEqual(session.scalar(select(func.count()).select_from(Bookmark)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(ChatSession)), 0)

    async def test_delete_cors_and_origin_protection(self):
        response = await self.a.options("/api/v1/user/chats/example", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "DELETE",
        })
        self.assertEqual(response.status_code, 200)
        chat_id = await self.new_chat()
        response = await self.a.delete(f"/api/v1/user/chats/{chat_id}", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 403)
