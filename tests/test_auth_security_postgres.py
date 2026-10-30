from concurrent.futures import ProcessPoolExecutor
import ipaddress
import os
import time
import unittest
from unittest.mock import patch
import uuid

import httpx
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from backend.api import auth
from backend.core import auth_rate_limit
from backend.db.auth_models import User
from backend.db.auth_security_models import AuthRateLimit
from backend.db.database import get_db
from backend.main import app
from security_client import SecurityClient
from test_idempotency_postgres import _test_database_url


TEST_SECRET = "postgres-auth-security-test-secret-with-sufficient-length"
PASSWORD = "Native PostgreSQL security test password 123!"


def auth_from_process(arguments):
    ip, email, password, action, now = arguments
    engine = create_engine(_test_database_url(), pool_timeout=5, connect_args={"connect_timeout": 10})

    def local_db():
        with Session(engine) as db:
            yield db

    async def send():
        async with SecurityClient(transport=httpx.ASGITransport(app=app, client=(ip, 1234)), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/" + action, json={"email": email, "password": password}, headers={"Origin": "http://localhost:3000"})
            return {"status": response.status_code, "retry_after": response.headers.get("retry-after"),
                    "body": response.json() if response.status_code != 200 else None,
                    "auth_cookie": auth.COOKIE_NAME in response.headers.get("set-cookie", "")}

    import asyncio
    app.dependency_overrides[get_db] = local_db
    try:
        with patch.object(auth.settings, "JWT_SECRET_KEY", TEST_SECRET), \
                patch.object(auth.settings, "AUTH_COOKIE_SECURE", False), \
                patch.object(auth.settings, "TRUSTED_PROXY_CIDRS", []), \
                patch.object(auth_rate_limit, "epoch", lambda db: now), \
                patch.object(auth, "password_hash", wraps=auth.password_hash) as hasher:
            result = asyncio.run(send())
            result["verifications"] = hasher.verify_and_update.call_count
            result["hashes"] = hasher.hash.call_count
            return result
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


@unittest.skipUnless(os.getenv("NAFSIY_RUN_POSTGRES_TESTS") == "1", "Explicit PostgreSQL test opt-in required")
class PostgreSQLAuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(_test_database_url(), pool_timeout=5, connect_args={"connect_timeout": 10})
        self.marker = uuid.uuid4().hex
        self.emails = [f"auth-security-{self.marker}-{i}@example.com" for i in range(12)]
        self.ips = [str(ipaddress.IPv6Address((0xfd << 120) | (uuid.uuid4().int & ((1 << 120) - 1)))) for _ in range(3)]
        self.now = int(time.time())
        with Session(self.engine) as db:
            db.add(User(email=self.emails[0], password_hash=auth.password_hash.hash(PASSWORD)))
            db.commit()

    def tearDown(self):
        try:
            with Session(self.engine) as db:
                db.execute(delete(User).where(User.email.in_(self.emails)))
                with patch.object(auth.settings, "JWT_SECRET_KEY", TEST_SECRET):
                    for ip in self.ips:
                        group = str(ipaddress.ip_network(ip + "/64", strict=False))
                        for scope in ("bootstrap-ip", "login-ip", "register-ip"):
                            prefix = scope + ":" + auth_rate_limit.digest(scope, group) + ":%"
                            db.execute(delete(AuthRateLimit).where(AuthRateLimit.bucket_key.like(prefix)))
                        for email in self.emails:
                            for scope in ("login-pair", "register-pair"):
                                prefix = scope + ":" + auth_rate_limit.digest(scope, email + "\0" + group) + ":%"
                                db.execute(delete(AuthRateLimit).where(AuthRateLimit.bucket_key.like(prefix)))
                db.commit()
        finally:
            self.engine.dispose()

    def test_failed_login_limit_is_atomic_and_known_unknown_responses_match(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            groups = []
            for email in self.emails[:2]:
                results = list(pool.map(auth_from_process, [(self.ips[0], email, "Wrong password", "login", self.now)] * 8))
                self.assertEqual(sorted(result["status"] for result in results), [401] * 5 + [429] * 3)
                self.assertEqual(sum(result["verifications"] for result in results), 5)
                self.assertTrue(all(int(result["retry_after"]) > 0 for result in results if result["status"] == 429))
                self.assertTrue(all(not result["auth_cookie"] for result in results))
                groups.append({result["status"]: result["body"] for result in results})
            self.assertEqual(groups[0], groups[1])
        legitimate = auth_from_process((self.ips[1], self.emails[0], PASSWORD, "login", self.now))
        self.assertEqual(legitimate["status"], 200)

    def test_registration_ip_limit_is_atomic_across_four_processes(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(auth_from_process, [
                (self.ips[0], email, PASSWORD, "register", self.now) for email in self.emails[1:9]
            ]))
        self.assertEqual(sorted(result["status"] for result in results), [202] * 5 + [429] * 3)
        self.assertEqual(sum(result["hashes"] for result in results), 5)
        self.assertTrue(all(not result["auth_cookie"] for result in results))
        with Session(self.engine) as db:
            created = db.scalars(select(User.id).where(User.email.in_(self.emails[1:9]))).all()
            self.assertEqual(len(created), 5)

    def test_registration_pair_limit_and_duplicate_race_are_generic(self):
        email = self.emails[1]
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(auth_from_process, [(self.ips[0], email, PASSWORD, "register", self.now)] * 8))
        self.assertEqual(sorted(result["status"] for result in results), [202] * 3 + [429] * 5)
        self.assertEqual(sum(result["hashes"] for result in results), 3)
        accepted = [result["body"] for result in results if result["status"] == 202]
        self.assertTrue(all(body == accepted[0] for body in accepted))
        with Session(self.engine) as db:
            self.assertEqual(len(db.scalars(select(User.id).where(User.email == email)).all()), 1)
