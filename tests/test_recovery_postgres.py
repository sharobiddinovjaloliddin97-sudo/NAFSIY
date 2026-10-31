from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import unittest
import uuid
from fastapi import HTTPException
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from backend.core.recovery import consume, digest
from backend.db.auth_models import User, RecoveryToken
from test_idempotency_postgres import _test_database_url


@unittest.skipUnless(os.getenv("NAFSIY_RUN_POSTGRES_TESTS") == "1", "Explicit PostgreSQL opt-in required")
class PostgreSQLRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine(_test_database_url(), pool_size=2, max_overflow=0, pool_timeout=5)
        self.user=uuid.uuid4().hex
        self.token=uuid.uuid4().hex+uuid.uuid4().hex
        with Session(self.engine) as db:
            db.add(User(id=self.user,email=self.user+"@example.invalid",password_hash="synthetic")); db.flush()
            db.add(RecoveryToken(digest=digest(self.token),user_id=self.user,purpose="reset",version=0,
                                 expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
            db.commit()

    def tearDown(self):
        with Session(self.engine) as db:
            db.execute(delete(User).where(User.id==self.user));db.commit()
        self.engine.dispose()

    def test_atomic_single_use_reset_under_pool_contention(self):
        def reset(_):
            with Session(self.engine) as db:
                try:
                    consume(db,self.token,"reset","synthetic-new-hash")
                    return 200
                except HTTPException as error:
                    return error.status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(reset,range(16)))
        self.assertEqual(results.count(200),1)
        self.assertEqual(results.count(400),15)
        self.assertEqual(self.engine.pool.checkedout(),0)
        with Session(self.engine) as db:
            self.assertEqual(db.get(User,self.user).auth_version,1)
            self.assertIsNone(db.get(RecoveryToken,digest(self.token)))
