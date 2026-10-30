import asyncio
from datetime import datetime, timedelta, timezone
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import httpx
import jwt
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend.api import ai, auth
from backend.core import auth_maintenance, auth_rate_limit, csrf
from backend.core.auth_tokens import decode_session
from backend.core.request_security import client_ip, rate_ip
from backend.db.auth_models import User
from backend.db.auth_security_models import AuthRateLimit
from backend.db.database import Base, get_db
from backend.db.user_data_models import Bookmark, ChatMessage, ChatSession
from backend.main import app
from security_client import SecurityClient


class AuthSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)

        def local_db():
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = local_db
        self.now = int(time.time())
        self.patches = [
            patch.object(auth.settings, "JWT_SECRET_KEY", "auth-security-test-secret-with-sufficient-length"),
            patch.object(auth.settings, "AUTH_COOKIE_SECURE", False),
            patch.object(auth.settings, "TRUSTED_PROXY_CIDRS", []),
            patch.object(auth_rate_limit, "time", SimpleNamespace(time=lambda: self.now)),
        ]
        for item in self.patches:
            item.start()
        self.password = "Security testing password 123!"
        self.user_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
        with Session(self.engine) as db:
            for user_id, email in zip(self.user_ids, ("alice@example.com", "bob@example.com")):
                db.add(User(id=user_id, email=email, password_hash=auth.password_hash.hash(self.password)))
            db.commit()
            self.chat_id = uuid.uuid4().hex
            db.add(ChatSession(id=self.chat_id, user_id=self.user_ids[0]))
            db.commit()
        self.clients = []
        self.a, self.b = self.client(), self.client()
        for client, email in ((self.a, "alice@example.com"), (self.b, "bob@example.com")):
            self.assertEqual((await client.post("/api/v1/auth/login", json={"email": email, "password": self.password})).status_code, 200)

    def client(self, ip="127.0.0.1", *, secure=True):
        cls = SecurityClient if secure else httpx.AsyncClient
        client = cls(transport=httpx.ASGITransport(app=app, client=(ip, 1234)), base_url="http://test")
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    async def tokens(self, client):
        response = await client.get("/api/v1/auth/csrf", headers={"X-CSRF-Request": "1"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def raw(self, client, method, path, **kwargs):
        return await httpx.AsyncClient.request(client, method, path, **kwargs)

    async def test_every_authenticated_write_rejects_missing_csrf(self):
        bookmark = await self.a.post("/api/v1/user/bookmarks", json={"kind": "quran", "reference": "1:1", "title": "Test"})
        routes = [
            ("POST", "/api/v1/auth/logout", None),
            ("POST", "/api/v1/user/chats", {}),
            ("DELETE", f"/api/v1/user/chats/{self.chat_id}", None),
            ("POST", "/api/v1/user/bookmarks", {"kind": "quran", "reference": "1:2", "title": "Test"}),
            ("DELETE", "/api/v1/user/bookmarks/" + bookmark.json()["id"], None),
            ("POST", f"/api/v1/ai/chats/{self.chat_id}/send", {"message": "Question"}),
        ]
        with patch.object(ai, "ask_nafsiy", AsyncMock()) as ask:
            for method, path, body in routes:
                response = await self.raw(self.a, method, path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
                self.assertEqual(response.status_code, 403, path)
                self.assertEqual(response.json(), {"detail": csrf.FAILURE})
            ask.assert_not_awaited()
        self.assertEqual((await self.a.get(f"/api/v1/user/chats/{self.chat_id}")).status_code, 200)

    async def test_invalid_and_expired_csrf_share_same_403(self):
        raw = self.a.cookies.get(auth.COOKIE_NAME)
        with patch.object(csrf, "time", SimpleNamespace(time=lambda: int(time.time()) - csrf.TOKEN_SECONDS - 10)):
            expired = csrf.issue_token("session", raw)
        for value in ("bad-token", expired, (await self.tokens(self.a))["csrf_token"][:-1] + "x"):
            response = await self.raw(self.a, "POST", "/api/v1/user/chats", json={}, headers={"X-CSRF-Token": value})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {"detail": csrf.FAILURE})

    async def test_cross_user_and_same_user_new_session_tokens_are_isolated(self):
        first_jwt = self.a.cookies.get(auth.COOKIE_NAME)
        old = (await self.tokens(self.a))["csrf_token"]
        foreign = await self.raw(self.b, "POST", "/api/v1/user/chats", json={}, headers={"X-CSRF-Token": old})
        self.assertEqual(foreign.status_code, 403)
        login = await self.a.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": self.password})
        self.assertEqual(login.status_code, 200)
        second_jwt = self.a.cookies.get(auth.COOKIE_NAME)
        self.assertNotEqual(first_jwt, second_jwt)
        self.assertNotEqual(decode_session(first_jwt)["jti"], decode_session(second_jwt)["jti"])
        stale = await self.raw(self.a, "POST", "/api/v1/user/chats", json={}, headers={"X-CSRF-Token": old})
        self.assertEqual(stale.status_code, 403)
        self.assertEqual((await self.a.post("/api/v1/user/chats", json={})).status_code, 201)

    async def test_login_csrf_requires_own_pre_session(self):
        attacker, victim = self.client(), self.client(secure=False)
        stolen = (await self.tokens(attacker))["auth_csrf_token"]
        for headers in ({}, {"X-CSRF-Token": stolen}):
            response = await victim.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": self.password}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual((await victim.get("/api/v1/auth/me")).status_code, 401)

    async def test_anonymous_and_session_token_modes_cannot_be_exchanged(self):
        tokens = await self.tokens(self.a)
        wrong_mode = await self.raw(self.a, "POST", "/api/v1/user/chats", json={}, headers={"X-CSRF-Token": tokens["auth_csrf_token"]})
        self.assertEqual(wrong_mode.status_code, 403)
        login = await self.raw(self.a, "POST", "/api/v1/auth/login", json={"email": "alice@example.com", "password": self.password}, headers={"X-CSRF-Token": tokens["csrf_token"]})
        self.assertEqual(login.status_code, 403)

    async def test_bootstrap_requires_custom_header_and_rejects_cross_origin(self):
        for headers in ({}, {"X-CSRF-Request": "1", "Origin": "https://evil.example"},
                        {"X-CSRF-Request": "1", "Origin": "null"},
                        {"X-CSRF-Request": "1", "Sec-Fetch-Site": "cross-site"}):
            response = await self.a.get("/api/v1/auth/csrf", headers=headers)
            self.assertEqual(response.status_code, 403)
        response = await self.a.get("/api/v1/auth/csrf", headers={"X-CSRF-Request": "1", "Origin": "http://localhost:3000"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("Cookie", response.headers["vary"])
        self.assertNotIn(self.a.cookies.get(auth.COOKIE_NAME), response.text)
        self.assertNotIn(auth.settings.JWT_SECRET_KEY, response.text)

    async def test_cross_origin_login_and_logout_rejected_even_with_valid_csrf(self):
        tokens = await self.tokens(self.a)
        for path, token, body in (("/login", tokens["auth_csrf_token"], {"email": "alice@example.com", "password": self.password}),
                                  ("/logout", tokens["csrf_token"], None)):
            response = await self.raw(self.a, "POST", "/api/v1/auth" + path, json=body,
                                      headers={"Origin": "https://evil.example", "X-CSRF-Token": token})
            self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.a.get("/api/v1/auth/me")).status_code, 200)
        duplicate_origin = await self.raw(self.a, "POST", "/api/v1/auth/logout", headers=[
            ("Origin", "http://localhost:3000"), ("Origin", "https://evil.example"),
            ("X-CSRF-Token", tokens["csrf_token"]),
        ])
        self.assertEqual(duplicate_origin.status_code, 403)

    async def test_expired_pre_session_context_and_header_fail(self):
        with patch.object(csrf, "time", SimpleNamespace(time=lambda: int(time.time()) - csrf.CONTEXT_SECONDS - 20)):
            context = csrf.issue_token("context", "anonymous-bootstrap", lifetime=csrf.CONTEXT_SECONDS)
            header = csrf.issue_token("anonymous", context)
        client = self.client(secure=False)
        response = await client.post("/api/v1/auth/register", json={"email": "new@example.com", "password": self.password}, headers={
            "Cookie": f"{csrf.CONTEXT_COOKIE}={context}", "X-CSRF-Token": header,
        })
        self.assertEqual(response.status_code, 403)

    async def test_logout_clears_cookies_and_old_header_cannot_authorize_guest(self):
        token = (await self.tokens(self.a))["csrf_token"]
        response = await self.raw(self.a, "POST", "/api/v1/auth/logout", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.a.cookies.get(auth.COOKIE_NAME))
        self.assertIsNone(self.a.cookies.get(csrf.CONTEXT_COOKIE))
        response = await self.raw(self.a, "POST", "/api/v1/user/chats", json={}, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 401)

    async def test_bookmark_mutations_require_valid_token_and_preserve_ownership(self):
        created = await self.a.post("/api/v1/user/bookmarks", json={"kind": "quran", "reference": "2:255", "title": "Test"})
        self.assertEqual(created.status_code, 201)
        path = "/api/v1/user/bookmarks/" + created.json()["id"]
        self.assertEqual((await self.raw(self.a, "DELETE", path)).status_code, 403)
        self.assertEqual((await self.b.delete(path)).status_code, 404)
        self.assertEqual((await self.a.delete(path)).status_code, 204)

    async def test_ai_needs_csrf_and_idempotency_and_replay_preserves_both(self):
        token = (await self.tokens(self.a))["csrf_token"]
        path = f"/api/v1/ai/chats/{self.chat_id}/send"
        answer = {"answer": "Test answer", "sources": [], "model": "test"}
        with patch.object(ai, "ask_nafsiy", AsyncMock(return_value=answer)) as ask:
            missing_key = await self.raw(self.a, "POST", path, json={"message": "Question"}, headers={"X-CSRF-Token": token})
            self.assertEqual(missing_key.status_code, 422)
            headers = {"X-CSRF-Token": token, "Idempotency-Key": str(uuid.uuid4())}
            for _ in range(2):
                self.assertEqual((await self.raw(self.a, "POST", path, json={"message": "Question"}, headers=headers)).status_code, 200)
            ask.assert_awaited_once()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ChatMessage)), 2)

    async def test_cors_allows_csrf_only_for_explicit_origins(self):
        for origin, expected in (("http://localhost:3000", 200), ("https://evil.example", 400)):
            response = await self.a.options("/api/v1/auth/login", headers={"Origin": origin,
                "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-CSRF-Token,Content-Type"})
            self.assertEqual(response.status_code, expected)
            if expected == 200:
                self.assertEqual(response.headers["access-control-allow-origin"], origin)
                self.assertIn("x-csrf-token", response.headers["access-control-allow-headers"].lower())
            else:
                self.assertNotIn("access-control-allow-origin", response.headers)

    async def test_registration_response_does_not_enumerate_or_auto_login(self):
        client = self.client("198.51.100.23")
        bodies = []
        with patch.object(auth, "password_hash", wraps=auth.password_hash) as hasher:
            for email in ("brandnew@example.com", "alice@example.com"):
                response = await client.post("/api/v1/auth/register", json={"email": email, "password": self.password})
                self.assertEqual(response.status_code, 202)
                self.assertNotIn(auth.COOKIE_NAME, response.headers.get("set-cookie", ""))
                bodies.append(response.json())
            self.assertEqual(hasher.hash.call_count, 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertNotIn("id", bodies[0])
        self.assertEqual((await client.get("/api/v1/auth/me")).status_code, 401)

    async def test_unknown_and_known_failed_login_match_through_rate_limit(self):
        client = self.client("198.51.100.24")
        results = []
        with patch.object(auth, "password_hash", wraps=auth.password_hash) as hasher:
            for email in ("alice@example.com", "unknown@example.com"):
                statuses = []
                for _ in range(6):
                    response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Wrong password"})
                    statuses.append((response.status_code, response.json(), response.headers.get("retry-after")))
                results.append(statuses)
            self.assertEqual(hasher.verify_and_update.call_count, 10)
        self.assertEqual(results[0], results[1])
        self.assertEqual([item[0] for item in results[0]], [401] * 5 + [429])

    async def test_per_ip_limit_covers_email_rotation(self):
        client = self.client("198.51.100.25")
        with patch.object(auth_rate_limit, "LOGIN_IP_LIMIT", 3), patch.object(auth, "password_hash", wraps=auth.password_hash) as hasher:
            for index in range(4):
                response = await client.post("/api/v1/auth/login", json={"email": f"unknown{index}@example.com", "password": "Wrong password"})
                self.assertEqual(response.status_code, 401 if index < 3 else 429)
            self.assertEqual(hasher.verify_and_update.call_count, 3)

    async def test_failures_on_another_ip_do_not_lock_account_and_windows_reset(self):
        attacker, legitimate = self.client("198.51.100.26"), self.client("198.51.100.27")
        for _ in range(5):
            await attacker.post("/api/v1/auth/login", json={"email": "ALICE@example.com", "password": "Wrong"})
        self.assertEqual((await attacker.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": self.password})).status_code, 429)
        self.assertEqual((await legitimate.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": self.password})).status_code, 200)
        self.now += auth_rate_limit.LOGIN_WINDOW
        self.assertEqual((await attacker.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": self.password})).status_code, 200)

    async def test_successful_logins_refund_pair_slots(self):
        client = self.client("198.51.100.28")
        for _ in range(8):
            response = await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": self.password})
            self.assertEqual(response.status_code, 200)
        prefix = "login-pair:" + auth_rate_limit.digest("login-pair", "alice@example.com\0" + "198.51.100.28") + ":%"
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(AuthRateLimit.count).where(AuthRateLimit.bucket_key.like(prefix))), 0)

    async def test_registration_limits_are_generic_and_bounded(self):
        client = self.client("198.51.100.29")
        with patch.object(auth_rate_limit, "REGISTER_IP_LIMIT", 3):
            for index in range(4):
                response = await client.post("/api/v1/auth/register", json={"email": f"register{index}@example.com", "password": self.password})
                self.assertEqual(response.status_code, 202 if index < 3 else 429)
            self.assertGreater(int(response.headers["retry-after"]), 0)

    async def test_limiter_keys_are_hmac_only_and_expired_rows_are_purged(self):
        with Session(self.engine) as db:
            keys = db.scalars(select(AuthRateLimit.bucket_key)).all()
            self.assertTrue(keys)
            self.assertTrue(all("alice" not in key and "bob" not in key and "127.0.0.1" not in key for key in keys))
            db.add(AuthRateLimit(bucket_key="expired-test", count=1, expires_at=datetime.now(timezone.utc) - timedelta(hours=1)))
            db.commit()
            self.assertEqual(auth_rate_limit.purge_expired(db), 1)
            self.assertIsNone(db.get(AuthRateLimit, "expired-test"))
            self.assertEqual(db.scalar(select(func.count()).select_from(User)), 2)

    async def test_maintenance_purges_expired_rows_without_user_data_changes(self):
        with Session(self.engine) as db:
            db.add(AuthRateLimit(bucket_key="expired-maintenance", count=1, expires_at=datetime.now(timezone.utc) - timedelta(hours=1)))
            db.commit()
        with patch.object(auth_maintenance, "INTERVAL_SECONDS", 0.01):
            task = asyncio.create_task(auth_maintenance.cleanup_loop(self.engine))
            try:
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    with Session(self.engine) as db:
                        if db.get(AuthRateLimit, "expired-maintenance") is None:
                            break
                else:
                    self.fail("Expired limiter row not purged")
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def test_jwt_expiration_and_legacy_sessions_require_reauthentication(self):
        now = datetime.now(timezone.utc)
        claims = {"sub": self.user_ids[0], "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1),
                  "iss": auth.JWT_ISSUER, "aud": auth.JWT_AUDIENCE, "jti": uuid.uuid4().hex}
        for payload in (claims, {key: value for key, value in {**claims, "iat": now, "exp": now + timedelta(hours=1)}.items() if key != "jti"}):
            token = jwt.encode(payload, auth.settings.JWT_SECRET_KEY, algorithm="HS256")
            response = await self.a.get("/api/v1/auth/me", headers={"Cookie": f"{auth.COOKIE_NAME}={token}"})
            self.assertEqual(response.status_code, 401)
            response = await self.a.get("/api/v1/auth/csrf", headers={"Cookie": f"{auth.COOKIE_NAME}={token}", "X-CSRF-Request": "1"})
            self.assertIsNone(response.json()["csrf_token"])

    async def test_bootstrap_rate_limit_and_refill(self):
        client = self.client("198.51.100.30", secure=False)
        with patch.object(auth, "BOOTSTRAP_LIMIT", 2):
            for index in range(3):
                response = await client.get("/api/v1/auth/csrf", headers={"X-CSRF-Request": "1"})
                self.assertEqual(response.status_code, 200 if index < 2 else 429)
            self.now += 60
            self.assertEqual((await client.get("/api/v1/auth/csrf", headers={"X-CSRF-Request": "1"})).status_code, 200)

    async def test_spoofed_forwarded_headers_cannot_reset_login_limit(self):
        client = self.client("198.51.100.61")
        for index in range(6):
            response = await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "Wrong"},
                                         headers={"X-Forwarded-For": f"203.0.113.{index + 1}"})
            self.assertEqual(response.status_code, 401 if index < 5 else 429)

    async def test_trusted_proxy_ignores_spoofed_leftmost_hops_in_real_requests(self):
        client = self.client("10.0.0.1")
        with patch.object(auth.settings, "TRUSTED_PROXY_CIDRS", ["10.0.0.1/32"]):
            for index in range(6):
                response = await client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "Wrong"},
                                             headers={"X-Forwarded-For": f"192.0.2.{index + 1}, 198.51.100.62"})
                self.assertEqual(response.status_code, 401 if index < 5 else 429)

    async def test_unknown_password_hash_is_generic_unauthorized(self):
        with Session(self.engine) as db:
            db.get(User, self.user_ids[0]).password_hash = "unusable-hash"
            db.commit()
        response = await self.a.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "Wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid email or password"})


class ProxySecurityTests(unittest.TestCase):
    def request(self, peer, xff=None, **headers):
        if xff is not None:
            headers["x-forwarded-for"] = xff
        return Request({"type": "http", "method": "POST", "path": "/", "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()], "client": (peer, 1234), "scheme": "http", "server": ("test", 80), "query_string": b""})

    def test_untrusted_peer_cannot_spoof_forwarded_headers(self):
        with patch.object(auth.settings, "TRUSTED_PROXY_CIDRS", []):
            self.assertEqual(client_ip(self.request("198.51.100.1", "203.0.113.9", forwarded="for=203.0.113.8")), "198.51.100.1")

    def test_trusted_proxy_walks_chain_from_right_to_first_untrusted(self):
        with patch.object(auth.settings, "TRUSTED_PROXY_CIDRS", ["10.0.0.1/32", "10.0.0.2/32"]):
            self.assertEqual(client_ip(self.request("10.0.0.1", "192.0.2.99, 203.0.113.7, 10.0.0.2")), "203.0.113.7")
            self.assertEqual(client_ip(self.request("10.0.0.1", "bad-value")), "10.0.0.1")
            self.assertEqual(client_ip(self.request("10.0.0.1", "garbage, 203.0.113.7, 10.0.0.2")), "203.0.113.7")
            repeated = self.request("10.0.0.1", "192.0.2.99")
            repeated.scope["headers"].append((b"x-forwarded-for", b"203.0.113.7"))
            self.assertEqual(client_ip(repeated), "203.0.113.7")

    def test_ipv6_network_grouping_and_mapped_ipv4_are_canonical(self):
        with patch.object(auth.settings, "TRUSTED_PROXY_CIDRS", []):
            self.assertEqual(rate_ip(self.request("2001:db8:1:2::1")), rate_ip(self.request("2001:db8:1:2::ff")))
            self.assertEqual(client_ip(self.request("::ffff:192.0.2.10")), "192.0.2.10")
