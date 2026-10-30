import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.api import ai, auth
from backend.core.config import settings
from backend.db.auth_models import User
from backend.db.user_data_models import ChatSession, ChatMessage
from backend.db.operations_models import AIOperation
import test_ai_send as send_fixtures


class AccountDeletionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = send_fixtures.AISendTests.asyncSetUp
    asyncTearDown = send_fixtures.AISendTests.asyncTearDown

    async def erase(self, **changes):
        return await self.a.request("DELETE", "/api/v1/user/account", json={
            "confirmation": "DELETE MY ACCOUNT", "password": "Testing password123!", **changes})

    async def test_disabled_feature_and_explicit_confirmation(self):
        with patch.object(settings, "ACCOUNT_DELETION_ENABLED", False):
            self.assertEqual((await self.erase()).status_code, 503)
        with patch.object(settings, "ACCOUNT_DELETION_ENABLED", True):
            self.assertEqual((await self.erase(confirmation="yes")).status_code, 422)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(User)), 2)

    async def test_authentication_csrf_origin_and_reauthentication(self):
        body = {"confirmation": "DELETE MY ACCOUNT", "password": "Testing password123!"}
        with patch.object(settings, "ACCOUNT_DELETION_ENABLED", True):
            self.assertEqual((await self.guest.request("DELETE", "/api/v1/user/account", json=body)).status_code, 401)
            self.assertEqual((await self.a.request("DELETE", "/api/v1/user/account", json=body,
                headers={"X-CSRF-Token": "invalid"})).status_code, 403)
            self.assertEqual((await self.a.request("DELETE", "/api/v1/user/account", json=body,
                headers={"Origin": "https://untrusted.example"})).status_code, 403)
            self.assertEqual((await self.erase(password="wrong")).status_code, 401)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(User, self.user_ids[0]))

    async def test_delete_clears_cookie_invalidates_old_session_and_preserves_other_account(self):
        old = self.a.cookies.get(auth.COOKIE_NAME)
        with patch.object(settings, "ACCOUNT_DELETION_ENABLED", True):
            response = await self.erase()
        self.assertEqual(response.status_code, 204)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual((await self.a.get("/api/v1/auth/me")).status_code, 401)
        self.assertEqual((await self.guest.get("/api/v1/auth/me", headers={"Cookie": f"{auth.COOKIE_NAME}={old}"})).status_code, 401)
        self.assertEqual((await self.b.get("/api/v1/auth/me")).status_code, 200)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ChatSession)), 0)

    async def test_inflight_ai_cannot_persist_after_account_deletion(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def generate(**kwargs):
            entered.set()
            await finish.wait()
            return self.answer
        with patch.object(settings, "ACCOUNT_DELETION_ENABLED", True), patch.object(ai, "ask_nafsiy", side_effect=generate):
            pending = asyncio.create_task(self.a.post(self.path, json={"message": "Synthetic private question"}))
            await asyncio.wait_for(entered.wait(), 5)
            response = await self.erase()
            self.assertEqual(response.status_code, 204)
            finish.set()
            result = await asyncio.wait_for(pending, 5)
        self.assertIn(result.status_code, (404, 409))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ChatMessage)), 0)
            self.assertTrue(all(row.request_id is None for row in db.scalars(select(AIOperation))))

    async def test_reauthentication_rate_limit_is_independent_and_fails_closed(self):
        with patch.object(settings, "ACCOUNT_DELETION_ENABLED", True):
            for _ in range(3):
                self.assertEqual((await self.erase(password="wrong")).status_code, 401)
            self.assertEqual((await self.erase()).status_code, 429)
