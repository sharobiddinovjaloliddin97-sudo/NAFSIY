import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import jwt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api import auth
from backend.db.auth_models import User
from backend.db.auth_security_models import AuthRateLimit
from security_client import SecurityClient
from backend.db.database import get_db
from backend.main import app


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        User.__table__.create(self.engine)
        from backend.db.auth_models import RecoveryToken
        RecoveryToken.__table__.create(self.engine)
        AuthRateLimit.__table__.create(self.engine)

        def local_db():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_db] = local_db
        self.secret = "test-only-secret-key-for-authentication-tests-with-sufficient-length"
        self.key_patch = patch.object(auth.settings, "JWT_SECRET_KEY", self.secret)
        self.cookie_patch = patch.object(auth.settings, "AUTH_COOKIE_SECURE", False)
        self.key_patch.start()
        self.cookie_patch.start()
        self.client = SecurityClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        )
        self.credentials = {"email": "Alice@Example.com", "password": "Testing password 123!"}

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.pop(get_db, None)
        self.key_patch.stop()
        self.cookie_patch.stop()
        self.engine.dispose()

    async def register(self):
        return await self.client.post("/api/v1/auth/register", json=self.credentials)

    async def login(self):
        return await self.client.post("/api/v1/auth/login", json=self.credentials)

    async def test_register_login_me_logout(self):
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)
        response = await self.register()
        self.assertEqual(response.status_code, 202)
        self.assertNotIn("id", response.json())
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)
        response = await self.login()
        self.assertEqual(response.status_code, 200)
        user = response.json()
        self.assertEqual(user["email"], "alice@example.com")
        self.assertEqual(len(user["id"]), 32)
        self.assertNotIn("password_hash", user)
        self.assertNotIn("password", user)
        with Session(self.engine) as session:
            record = session.get(User, user["id"])
            self.assertTrue(record.password_hash.startswith("$argon2id$"))
            self.assertNotEqual(record.password_hash, self.credentials["password"])
            self.assertTrue(auth.password_hash.verify(self.credentials["password"], record.password_hash))
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Max-Age=3600", cookie)
        self.assertNotIn("Secure", cookie)
        me = await self.client.get("/api/v1/auth/me")
        self.assertEqual(me.json()["id"], user["id"])
        self.assertEqual(me.headers["cache-control"], "no-store")
        logout = await self.client.post("/api/v1/auth/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertIn("Max-Age=0", logout.headers["set-cookie"])
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)
        login = await self.client.post("/api/v1/auth/login", json=self.credentials)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["id"], user["id"])

    async def test_duplicate_email_and_bad_credentials(self):
        await self.register()
        duplicate = await self.client.post("/api/v1/auth/register", json={
            **self.credentials, "email": "ALICE@example.com",
        })
        self.assertEqual(duplicate.status_code, 202)
        errors = []
        for data in [
            {**self.credentials, "password": "Wrong password"},
            {**self.credentials, "email": "missing@example.com"},
        ]:
            response = await self.client.post("/api/v1/auth/login", json=data)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("set-cookie", response.headers)
            errors.append(response.json())
        self.assertEqual(errors[0], errors[1])

    async def test_request_validation(self):
        for data in [
            {**self.credentials, "email": "invalid-email"},
            {**self.credentials, "password": "short"},
            {**self.credentials, "password": "x" * 129},
            {"email": "alice@example.com"},
        ]:
            with self.subTest(data=list(data)):
                response = await self.client.post("/api/v1/auth/register", json=data)
                self.assertEqual(response.status_code, 422)
        with Session(self.engine) as session:
            self.assertIsNone(session.scalar(select(User)))

    async def test_invalid_and_expired_tokens(self):
        registered = await self.register()
        registered = await self.login()
        user_id = registered.json()["id"]
        now = datetime.now(timezone.utc)
        claims = {"sub": user_id, "iat": now, "exp": now + timedelta(hours=1),
                  "iss": auth.JWT_ISSUER, "aud": auth.JWT_AUDIENCE, "jti": uuid.uuid4().hex}
        tokens = [
            "invalid-token",
            jwt.encode({**claims, "exp": now - timedelta(seconds=1)}, self.secret, algorithm="HS256"),
            jwt.encode(claims, "another-test-secret-for-token-validation", algorithm="HS256"),
            jwt.encode(claims, self.secret, algorithm="HS384"),
            jwt.encode({key: value for key, value in claims.items() if key != "exp"}, self.secret, algorithm="HS256"),
            jwt.encode({**claims, "aud": "other-app"}, self.secret, algorithm="HS256"),
            jwt.encode({**claims, "sub": "f" * 32}, self.secret, algorithm="HS256"),
        ]
        self.client.cookies.clear()
        for index, token in enumerate(tokens):
            with self.subTest(token=index):
                response = await self.client.get("/api/v1/auth/me", headers={
                    "Cookie": f"{auth.COOKIE_NAME}={token}",
                })
                self.assertEqual(response.status_code, 401)

    async def test_inactive_user(self):
        response = await self.register()
        response = await self.login()
        with Session(self.engine) as session:
            user = session.get(User, response.json()["id"])
            user.is_active = False
            session.commit()
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)
        response = await self.client.post("/api/v1/auth/login", json=self.credentials)
        self.assertEqual(response.status_code, 401)

    async def test_origin_checks_and_credentialed_cors(self):
        for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
            response = await self.client.options("/api/v1/auth/login", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
            self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        response = await self.client.post("/api/v1/auth/register", json=self.credentials, headers={
            "Origin": "https://untrusted.example",
        })
        self.assertEqual(response.status_code, 403)
        response = await self.client.post("/api/v1/auth/register", json=self.credentials, headers={
            "Origin": "http://localhost:3000",
        })
        self.assertEqual(response.status_code, 202)
        await self.login()
        response = await self.client.post("/api/v1/auth/logout", headers={"Origin": "http://localhost:8000"})
        self.assertEqual(response.status_code, 200)

    async def test_secure_cookie_and_unconfigured_secret(self):
        with patch.object(auth.settings, "AUTH_COOKIE_SECURE", True):
            self.client.base_url = "https://test"
            response = await self.register()
            response = await self.login()
            self.client.base_url = "http://test"
        self.assertIn("Secure", response.headers["set-cookie"])
        with patch.object(auth.settings, "JWT_SECRET_KEY", ""):
            response = await self.client.post("/api/v1/auth/register", json={
                **self.credentials, "email": "new@example.com",
            })
        self.assertEqual(response.status_code, 503)
        with Session(self.engine) as session:
            self.assertIsNone(session.scalar(select(User).where(User.email == "new@example.com")))
