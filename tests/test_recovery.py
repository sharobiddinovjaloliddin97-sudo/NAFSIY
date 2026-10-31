from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from sqlalchemy import select, update
from sqlalchemy.orm import Session
import test_auth
from backend.api import auth
from backend.db.auth_models import RecoveryToken, User
from backend.core.recovery import consume, request_link, digest


class RecoveryTests(test_auth.AuthTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.sent = []
        class Capture:
            def send(_, address, purpose, token):
                self.sent.append((address, purpose, token))
        self.delivery_patch = patch("backend.core.recovery.delivery", return_value=Capture())
        self.delivery_patch.start()

    async def asyncTearDown(self):
        self.delivery_patch.stop()
        await super().asyncTearDown()

    async def test_verification_single_use_and_login_requirement(self):
        await self.register()
        raw = self.sent[-1][2]
        with patch.object(auth.settings, "REQUIRE_EMAIL_VERIFICATION", True):
            self.assertEqual((await self.login()).status_code, 401)
            response = await self.client.post("/api/v1/auth/recovery/verify/confirm", json={"token": raw})
            self.assertEqual(response.status_code, 200)
            self.assertEqual((await self.login()).status_code, 200)
            self.assertEqual((await self.client.post("/api/v1/auth/recovery/verify/confirm", json={"token": raw})).status_code, 400)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(RecoveryToken, digest(raw)))
            self.assertTrue(db.scalar(select(User)).email_verified)

    async def test_reset_revokes_copied_cookie_and_all_links(self):
        await self.register(); await self.login()
        cookie = self.client.cookies.get(auth.COOKIE_NAME)
        for _ in range(2):
            self.assertEqual((await self.client.post("/api/v1/auth/recovery/reset/request", json={"email": "alice@example.com"})).status_code, 202)
        first, second = self.sent[-2][2], self.sent[-1][2]
        response = await self.client.post("/api/v1/auth/recovery/reset/confirm", json={"token": first, "password": "A changed password 456!"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get("/api/v1/auth/me", headers={"Cookie": f"{auth.COOKIE_NAME}={cookie}"})).status_code, 401)
        self.assertEqual((await self.client.post("/api/v1/auth/recovery/reset/confirm", json={"token": second, "password": "Another password 789!"})).status_code, 400)
        self.assertEqual((await self.login()).status_code, 401)
        self.assertEqual((await self.client.post("/api/v1/auth/login", json={**self.credentials, "password": "A changed password 456!"})).status_code, 200)

    async def test_logout_revokes_copied_jwt_and_bootstrap(self):
        await self.register(); await self.login()
        cookie = self.client.cookies.get(auth.COOKIE_NAME)
        self.assertEqual((await self.client.post("/api/v1/auth/logout")).status_code, 200)
        headers = {"Cookie": f"{auth.COOKIE_NAME}={cookie}"}
        self.assertEqual((await self.client.get("/api/v1/auth/me", headers=headers)).status_code, 401)
        response = await self.client.get("/api/v1/auth/csrf", headers={**headers, "X-CSRF-Request": "1"})
        self.assertIsNone(response.json()["csrf_token"])

    async def test_reset_racing_cookie_issuance_cannot_upgrade_old_password_login(self):
        await self.register()
        original_refund = auth.refund_successful_login
        def reset_during_login(db, key):
            original_refund(db, key)
            db.execute(update(User).values(auth_version=User.auth_version + 1,
                password_hash=auth.password_hash.hash("Changed during login 456!")))
            db.commit()
        with patch.object(auth, "refund_successful_login", side_effect=reset_during_login):
            self.assertEqual((await self.login()).status_code, 200)
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)

    async def test_expiry_purpose_generic_responses_and_csrf(self):
        await self.register()
        raw = self.sent[-1][2]
        response = await self.client.post("/api/v1/auth/recovery/reset/confirm", json={"token": raw, "password": "Another password 123!"})
        self.assertEqual(response.status_code, 400)
        with Session(self.engine) as db:
            db.execute(update(RecoveryToken).values(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))); db.commit()
        self.assertEqual((await self.client.post("/api/v1/auth/recovery/verify/confirm", json={"token": raw})).status_code, 400)
        known = await self.client.post("/api/v1/auth/recovery/reset/request", json={"email": "alice@example.com"})
        unknown = await self.client.post("/api/v1/auth/recovery/reset/request", json={"email": "missing@example.com"})
        self.assertEqual(known.json(), unknown.json())
        for path in ("reset/request", "verify/confirm", "reset/confirm"):
            response = await self.client.post("/api/v1/auth/recovery/"+path, json={}, headers={"X-CSRF-Token": "invalid"})
            self.assertEqual(response.status_code, 403)
        response = await self.client.post("/api/v1/auth/recovery/reset/request", json={"email":"alice@example.com"}, headers={"Origin":"https://evil.example"})
        self.assertEqual(response.status_code,403)
