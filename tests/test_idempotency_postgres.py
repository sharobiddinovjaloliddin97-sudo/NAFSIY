import asyncio
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from dotenv import dotenv_values
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session
from operations_fixture import cleanup_operations
from security_client import POSTGRES_TEST_SECRET, SecurityClient, cleanup_postgres_bootstrap


def _test_database_url():
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    test_url = os.getenv("TEST_DATABASE_URL") or values.get("TEST_DATABASE_URL")
    main_url = os.getenv("DATABASE_URL") or values.get("DATABASE_URL")
    if not test_url or not main_url:
        raise RuntimeError("Both test and main database configuration are required")
    test, main = urlsplit(test_url), urlsplit(main_url)
    if not test.hostname or not main.hostname or "-pooler" in test.hostname:
        raise RuntimeError("A direct PostgreSQL test connection is required")
    if (test.hostname.replace("-pooler", ""), test.port or 5432, test.path) == (
        main.hostname.replace("-pooler", ""), main.port or 5432, main.path,
    ):
        raise RuntimeError("Refusing to run PostgreSQL tests against the main database")
    return test_url.replace("postgresql://", "postgresql+psycopg://", 1)


def send_from_process(arguments):
    import httpx
    import jwt
    from backend.api import ai, auth
    from backend.core import leases
    from backend.db.database import get_db
    from backend.main import app

    user_id, session_id, request_key, message, *extra = arguments
    options = extra[0] if extra else {}
    engine = create_engine(_test_database_url(), pool_timeout=5, connect_args={
        "connect_timeout": 10, "application_name": f"nafsiy-test-{user_id}",
    })
    calls = 0

    def local_db():
        with Session(engine) as session:
            yield session

    async def generate(**kwargs):
        nonlocal calls
        calls += 1
        if options.get("entered") is not None:
            options["entered"].set()
        if options.get("finish") is not None:
            while not options["finish"].is_set():
                await asyncio.sleep(0.03)
        else:
            await asyncio.sleep(options.get("delay", 0.5))
        return {"answer": "PostgreSQL concurrency test answer", "sources": [], "model": "test"}

    async def send():
        now = datetime.now(timezone.utc)
        token = jwt.encode({
            "sub": user_id, "iat": now, "exp": now + timedelta(minutes=5),
            "iss": auth.JWT_ISSUER, "aud": auth.JWT_AUDIENCE, "jti": uuid.uuid4().hex,
        }, secret, algorithm="HS256")
        async with SecurityClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/v1/ai/chats/{session_id}/send", json={"message": message}, headers={
                "Cookie": f"{auth.COOKIE_NAME}={token}", "Idempotency-Key": request_key,
                "Origin": "http://localhost:3000",
            })
            return {"status": response.status_code, "body": response.json(), "model_calls": calls,
                    "retry_after": response.headers.get("retry-after")}

    secret = POSTGRES_TEST_SECRET
    app.dependency_overrides[get_db] = local_db
    try:
        with patch.object(auth.settings, "JWT_SECRET_KEY", secret), patch.object(ai, "ask_nafsiy", generate), \
                patch.object(leases, "LEASE_SECONDS", options.get("lease_seconds", 30)), \
                patch.object(leases, "RENEW_INTERVAL_SECONDS", options.get("renew_interval", 8)):
            return asyncio.run(send())
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


@unittest.skipUnless(os.getenv("NAFSIY_RUN_POSTGRES_TESTS") == "1", "Explicit PostgreSQL test opt-in required")
class PostgreSQLIdempotencyTests(unittest.TestCase):
    def test_same_key_from_four_processes_creates_one_turn_and_one_quota_use(self):
        from backend.db.ai_models import AIRateLimit
        from backend.db.auth_models import User
        from backend.db.idempotency_models import AIIdempotency
        from backend.db.user_data_models import ChatMessage, ChatSession

        engine = create_engine(_test_database_url(), connect_args={"connect_timeout": 15})
        user_id, session_id, request_key = uuid.uuid4().hex, uuid.uuid4().hex, str(uuid.uuid4())
        try:
            with Session(engine) as session:
                session.add(User(id=user_id, email=f"idempotency-{user_id}@example.com", password_hash="unusable-test-hash"))
                session.flush()
                session.add(ChatSession(id=session_id, user_id=user_id, title="Idempotency integration test"))
                session.commit()
            arguments = user_id, session_id, request_key, "Concurrent test question"
            with ProcessPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(send_from_process, [arguments] * 8))
            self.assertTrue(all(result["status"] in (200, 409) for result in results), results)
            self.assertEqual(sum(result["model_calls"] for result in results), 1)
            replay = send_from_process(arguments)
            self.assertEqual(replay["status"], 200)
            self.assertEqual(replay["model_calls"], 0)
            changed = send_from_process((user_id, session_id, request_key, "Different question"))
            self.assertEqual(changed["status"], 409)
            self.assertEqual(changed["model_calls"], 0)
            with Session(engine) as session:
                messages = session.scalars(select(ChatMessage).where(
                    ChatMessage.session_id == session_id,
                ).order_by(ChatMessage.position)).all()
                self.assertEqual([message.role for message in messages], ["user", "assistant"])
                record, = session.scalars(select(AIIdempotency).where(AIIdempotency.user_id == user_id)).all()
                self.assertEqual(record.status, "completed")
                self.assertEqual(record.request_key, request_key)
                quota = session.scalar(select(func.sum(AIRateLimit.count)).where(
                    AIRateLimit.bucket_key.like(f"{user_id}:%"),
                ))
                self.assertEqual(quota, 1)
        finally:
            with Session(engine) as session:
                cleanup_operations(session, user_id)
                session.execute(delete(User).where(User.id == user_id))
                session.execute(delete(AIRateLimit).where(AIRateLimit.bucket_key.like(f"{user_id}:%")))
                cleanup_postgres_bootstrap(session)
                session.commit()
                self.assertEqual(session.scalar(select(func.count()).select_from(AIIdempotency).where(
                    AIIdempotency.user_id == user_id,
                )), 0)
            engine.dispose()
