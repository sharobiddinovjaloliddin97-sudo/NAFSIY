from datetime import datetime, timezone
from importlib import import_module
import json
import unittest
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from backend.api import ai
from backend.core.idempotency import fingerprint
from backend.db.ai_models import AIRateLimit
from backend.db.auth_models import User
from backend.db.database import Base
from backend.db.idempotency_models import AIIdempotency
from backend.db.user_data_models import ChatSession


class GenerationMigrationTests(unittest.TestCase):
    def test_legacy_processing_and_completed_records_survive_recovery_migration(self):
        old = import_module("migrations.versions.7bbcad9d4644_add_ai_idempotency")
        new = import_module("migrations.versions.f9a045983bdf_add_generation_leases_and_recovery")
        self.assertEqual(new.down_revision, old.revision)
        engine = create_engine("sqlite://")
        user_id, chat_id = uuid.uuid4().hex, uuid.uuid4().hex
        pending_key, complete_key = str(uuid.uuid4()), str(uuid.uuid4())
        answer = {"answer": "Legacy completed answer", "sources": [], "model": "test"}
        try:
            existing = [table for table in Base.metadata.sorted_tables if table.name not in (
                "ai_idempotency", "chat_generation_leases",
            )]
            Base.metadata.create_all(engine, tables=existing)
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    old.upgrade()
                connection.execute(User.__table__.insert().values(id=user_id, email="legacy@example.com", password_hash="unusable"))
                connection.execute(ChatSession.__table__.insert().values(id=chat_id, user_id=user_id))
                for key, status, response in ((pending_key, "processing", None), (complete_key, "completed", json.dumps(answer))):
                    connection.execute(text(
                        "INSERT INTO ai_idempotency (id,user_id,session_id,request_key,request_hash,status,response_json,created_at,updated_at) "
                        "VALUES (:id,:user,:chat,:key,:hash,:status,:response,:now,:now)"
                    ), {"id": uuid.uuid4().hex, "user": user_id, "chat": chat_id, "key": key,
                        "hash": fingerprint("Question"), "status": status,
                        "response": response, "now": datetime.now(timezone.utc).isoformat(" ")})
                with Operations.context(MigrationContext.configure(connection)):
                    new.upgrade()
            with Session(engine) as db:
                records = db.scalars(select(AIIdempotency)).all()
                self.assertTrue(all(record.rate_charged and record.lease_owner is None and record.lease_version is None for record in records))
                replay = ai.reserve_turn(db, user_id, chat_id, complete_key, "Question")
                self.assertEqual(replay["state"], "replay")
                self.assertEqual(replay["response"], answer)
                recovered = ai.reserve_turn(db, user_id, chat_id, pending_key, "Question")
                self.assertEqual(recovered["state"], "reserved")
                self.assertEqual(db.scalar(select(func.count()).select_from(AIRateLimit)), 0)
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    new.downgrade()
                self.assertNotIn("chat_generation_leases", inspect(connection).get_table_names())
                self.assertNotIn("rate_charged", {column["name"] for column in inspect(connection).get_columns("ai_idempotency")})
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM ai_idempotency")), 2)
        finally:
            engine.dispose()
