from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from alembic import command
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session

from backend.db import readiness as ready
from backend.db.backups import create_backup, restore_backup, verify_backup, table_fingerprints
from backend.db.monitor import backup_health, database_health
from backend.db.performance import benchmark
from backend.api import ai
from backend.core.account_deletion import delete_account_data
from backend.core import ai_operations as ops
from backend.db.auth_models import User
from backend.db.user_data_models import ChatSession, ChatMessage, Bookmark
from backend.db.idempotency_models import AIIdempotency
from backend.db.operations_models import AIOperation, AIProviderAttempt, AIBudget
from backend.db.models import Surah, Ayah, Translation
from disposable_postgres import disposable_database


class ReadinessUnitTests(unittest.TestCase):
    def test_static_bootstrap_and_historical_revision_integrity(self):
        result = ready.verify_artifacts()
        self.assertEqual(result["baseline_revision"], ready.BASELINE)
        self.assertEqual(len(result["baseline_tables"]), 8)
        self.assertEqual(len(result["migration_sha256_lf"]), 6)

    def test_tampered_snapshot_fails_before_connecting(self):
        with patch.object(ready, "lf_hash", return_value="bad"):
            with self.assertRaises(ready.ReadinessError):
                ready.verify_artifacts()

    def test_backup_monitor_reports_missing_or_invalid_as_unverified(self):
        with tempfile.TemporaryDirectory() as root:
            values = backup_health(root)
            self.assertFalse(values["integrity"])
            self.assertFalse(values["restore_test_success"])


@unittest.skipUnless(os.getenv("NAFSIY_RUN_DISPOSABLE_TESTS") == "1", "Explicit disposable PostgreSQL opt-in required")
class DisposableDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.context = disposable_database()
        self.engine, self.url, self.name, self.nonce = self.context.__enter__()
        self.pg_bin = Path(os.environ["NAFSIY_TEST_PG_BIN"])

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def fresh(self, revision="head"):
        return ready.bootstrap(self.engine, self.url, self.name, self.nonce, revision)

    def test_fresh_install_matches_every_model_and_known_fingerprint(self):
        self.assertEqual(self.fresh(), ready.head())
        with self.engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            result = ready.preflight(connection)
            self.assertTrue(result["models_match"])
            self.assertTrue(result["schema_compatible_at_revision"])
            self.assertEqual(result["pending_migrations"], [])
            self.assertTrue(all(result["privileges"].values()))

    def test_nonempty_database_and_hidden_objects_fail_closed(self):
        for ddl in ("CREATE SCHEMA unexpected", "CREATE TYPE public.fixture_type AS ENUM ('x')", "CREATE TABLE public.fixture_table(id int)"):
            with self.engine.begin() as connection:
                nested = connection.begin_nested()
                connection.exec_driver_sql(ddl)
                with self.assertRaises(ready.ReadinessError):
                    ready.assert_empty(connection)
                nested.rollback()
        self.fresh()
        with self.assertRaises(ready.ReadinessError):
            self.fresh()

    def test_wrong_nonce_confirmation_and_primary_selection_fail_closed(self):
        for name, nonce in (("wrong", self.nonce), (self.name, "0" * 32)):
            with self.assertRaises(ready.ReadinessError):
                ready.bootstrap(self.engine, self.url, name, nonce)
        with self.engine.connect() as connection:
            ready.assert_empty(connection)
        with patch.object(ready, "configured_url", return_value=self.url), self.engine.connect() as connection:
            with self.assertRaises(ready.ReadinessError):
                ready.assert_disposable(connection, self.url, self.name, self.nonce)

    def test_bootstrap_failure_rolls_back_all_schema(self):
        with patch.object(ready.command, "upgrade", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.fresh()
        with self.engine.connect() as connection:
            ready.assert_empty(connection)

    def test_original_baseline_upgrade_preserves_account_and_messages(self):
        self.fresh(ready.BASELINE)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO auth_users VALUES(repeat('1',32),'fixture@example.invalid','unusable',true,now())")
            connection.exec_driver_sql("INSERT INTO chat_sessions VALUES(repeat('2',32),repeat('1',32),'Synthetic',now(),now())")
            connection.exec_driver_sql("INSERT INTO chat_messages VALUES(repeat('3',32),repeat('2',32),'user','Synthetic private fixture','[]',1,now())")
        ready.upgrade_disposable(self.engine, self.url, self.name, self.nonce)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM auth_users")), 1)
            self.assertEqual(connection.scalar(text("SELECT content FROM chat_messages")), "Synthetic private fixture")
            self.assertTrue(ready.schema_matches_models(connection))

    def test_unversioned_original_schema_backup_and_guarded_adoption(self):
        with self.engine.begin() as connection:
            ready.assert_disposable(connection, self.url, self.name, self.nonce)
            ready.assert_empty(connection)
            connection.exec_driver_sql(ready.SNAPSHOT.read_text(encoding="utf-8"))
            connection.exec_driver_sql("INSERT INTO auth_users VALUES(repeat('1',32),'fixture@example.invalid','unusable',true,now())")
        with self.engine.connect() as connection:
            result = ready.preflight(connection)
            self.assertTrue(result["schema_compatible_at_revision"])
            self.assertTrue(result["requires_baseline_adoption"])
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "private"
            create_backup(self.engine, self.url, directory, self.pg_bin)
            self.assertIsNone(verify_backup(directory)["alembic_revision"])
        ready.upgrade_disposable(self.engine, self.url, self.name, self.nonce)
        with self.engine.connect() as connection:
            self.assertEqual(ready.revision(connection), ready.head())
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM auth_users")), 1)

    def test_existing_schema_drift_blocks_upgrade_without_data_change(self):
        self.fresh(ready.BASELINE)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE auth_users ADD COLUMN unexpected integer")
        with self.assertRaises(ready.ReadinessError):
            ready.upgrade_disposable(self.engine, self.url, self.name, self.nonce)
        with self.engine.connect() as connection:
            self.assertEqual(ready.revision(connection), ready.BASELINE)

    def test_backup_restore_snapshot_hashes_and_nonempty_target_refusal(self):
        self.fresh()
        with Session(self.engine) as db:
            db.add(User(id="1" * 32, email="fixture@example.invalid", password_hash="unusable"))
            db.add(AIBudget(period="precision", spent_usd=Decimal("9007199254.7409910001"), reserved_usd=Decimal(0)))
            db.commit()
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "private"
            result = create_backup(self.engine, self.url, directory, self.pg_bin)
            self.assertFalse(result["restore_verified"])
            self.assertTrue(verify_backup(directory, self.pg_bin)["tables"]["auth_users"]["rows"] == 1)
            self.assertTrue(verify_backup(directory)["sequences"])
            before = verify_backup(directory)["tables"]["ai_budgets"]["sha256"]
            with Session(self.engine) as db:
                db.get(AIBudget, "precision").spent_usd += Decimal("0.0000000001")
                db.commit()
            with self.engine.connect() as connection:
                self.assertNotEqual(table_fingerprints(connection)["ai_budgets"]["sha256"], before)
            with disposable_database() as (engine, url, name, nonce):
                receipt = restore_backup(engine, url, name, nonce, directory, self.pg_bin)
                self.assertTrue(receipt["success"])
                self.assertFalse(receipt["ready_for_application"])
                self.assertTrue(backup_health(directory)["restore_test_success"])
                with self.assertRaises(ready.ReadinessError):
                    restore_backup(engine, url, name, nonce, directory, self.pg_bin)
            with self.assertRaises(ready.ReadinessError):
                restore_backup(self.engine, self.url, self.name, "0" * 32, directory, self.pg_bin)

    def test_corrupted_dump_is_rejected_without_touching_target(self):
        self.fresh()
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "private"
            create_backup(self.engine, self.url, directory, self.pg_bin)
            with (directory / "database.dump").open("ab") as stream:
                stream.write(b"corrupted fixture")
            with disposable_database() as (engine, url, name, nonce):
                with self.assertRaises(ready.ReadinessError):
                    restore_backup(engine, url, name, nonce, directory, self.pg_bin)
                with engine.connect() as connection:
                    ready.assert_empty(connection)

    def test_restore_sql_failure_rolls_back_and_does_not_issue_success_receipt(self):
        self.fresh()
        # Local superuser fixture deliberately violates an FK so pg_restore fails
        # while adding the FK inside its single transaction.
        with self.engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL session_replication_role=replica")
            connection.exec_driver_sql("INSERT INTO chat_sessions VALUES(repeat('2',32),repeat('1',32),'Synthetic orphan',now(),now())")
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "private"
            create_backup(self.engine, self.url, directory, self.pg_bin)
            with disposable_database() as (engine, url, name, nonce):
                with self.assertRaises(ready.ReadinessError):
                    restore_backup(engine, url, name, nonce, directory, self.pg_bin)
                with engine.connect() as connection:
                    ready.assert_empty(connection)
                self.assertFalse(backup_health(directory)["restore_test_success"])

    def test_client_major_mismatch_blocks_backup(self):
        self.fresh()
        with tempfile.TemporaryDirectory() as root, patch("backend.db.backups.client_major", return_value=1):
            with self.assertRaises(ready.ReadinessError):
                create_backup(self.engine, self.url, Path(root) / "private", self.pg_bin)

    def test_exported_snapshot_ignores_later_writes_and_backup_can_contain_deleted_accounts(self):
        import backend.db.backups as backups
        self.fresh()
        user_id = uuid.uuid4().hex
        with Session(self.engine) as db:
            db.add(User(id=user_id, email="snapshot@example.invalid", password_hash="unusable"))
            db.commit()
        original = backups.run_client
        def concurrent_writer(executable, args, *values, **options):
            if "--format=custom" in args:
                with Session(self.engine) as db:
                    db.add(User(id=uuid.uuid4().hex, email="later@example.invalid", password_hash="unusable"))
                    db.commit()
            return original(executable, args, *values, **options)
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "private"
            with patch.object(backups, "run_client", side_effect=concurrent_writer):
                create_backup(self.engine, self.url, directory, self.pg_bin)
            with Session(self.engine) as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(User)), 2)
                delete_account_data(db, user_id)
            with disposable_database() as (engine, url, name, nonce):
                receipt = restore_backup(engine, url, name, nonce, directory, self.pg_bin)
                self.assertFalse(receipt["ready_for_application"])
                with Session(engine) as db:
                    self.assertEqual(db.scalar(select(func.count()).select_from(User)), 1)
                    self.assertIsNotNone(db.get(User, user_id))

    def account_fixture(self):
        self.fresh()
        user, other, chat = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        with Session(self.engine) as db:
            db.add_all([User(id=user, email="delete@example.invalid", password_hash="unusable"),
                        User(id=other, email="other@example.invalid", password_hash="unusable")])
            db.flush()
            db.add(ChatSession(id=chat, user_id=user))
            db.add(Bookmark(user_id=user, kind="quran", reference="1:1", title="Synthetic"))
            db.commit()
            reserved = ai.reserve_turn(db, user, chat, str(uuid.uuid4()), "Synthetic deletion fixture")
            attempt = ops.start_attempt(db, reserved["lease"], "planner", 100, 256)
        return user, other, chat, reserved, attempt

    def test_account_delete_cascades_and_retains_only_unlinked_cost_audit(self):
        user, other, chat, reserved, attempt = self.account_fixture()
        with Session(self.engine) as db:
            surah = Surah(name_ar="Synthetic", name_en="Synthetic", ayah_count=1)
            db.add(surah)
            db.flush()
            ayah = Ayah(surah_id=surah.id, number=1, arabic_text="Synthetic fixture")
            db.add(ayah)
            db.flush()
            db.add(Translation(ayah_id=ayah.id, language="en", edition="fixture", translator="Synthetic", text="Synthetic fixture"))
            db.commit()
            delete_account_data(db, user)
        def stale(_):
            with Session(self.engine) as db:
                try:
                    ai.save_turn(db, user, chat, "Synthetic", ai.ChatResponse(answer="Synthetic", model="fixture"), reserved["lease"])
                    return 200
                except Exception as error:
                    return getattr(error, "status_code", 500)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(stale, range(4))), [404] * 4)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(User, user))
            self.assertIsNotNone(db.get(User, other))
            for model in (ChatSession, ChatMessage, Bookmark, AIIdempotency):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)
            operation = db.get(AIOperation, reserved["lease"].owner_id)
            self.assertIsNone(operation.request_id)
            self.assertEqual(operation.status, "account_deleted")
            self.assertEqual(db.get(AIProviderAttempt, attempt).status, "unknown_estimated")
            self.assertTrue(all(row.reserved_usd == 0 for row in db.scalars(select(AIBudget))))
            for model in (Surah, Ayah, Translation):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 1)
            retained = json.dumps({column.name: getattr(operation, column.name) for column in AIOperation.__table__.columns}, default=str)
            self.assertNotIn(user, retained)
            self.assertNotIn(chat, retained)
            self.assertNotIn("Synthetic deletion fixture", retained)

    def test_completion_and_account_deletion_serialize_without_orphan_persistence(self):
        user, other, chat, reserved, attempt = self.account_fixture()
        def save():
            with Session(self.engine) as db:
                try:
                    ai.save_turn(db, user, chat, "Synthetic", ai.ChatResponse(answer="Synthetic", model="fixture"), reserved["lease"])
                    return 200
                except Exception as error:
                    return getattr(error, "status_code", 500)
        def erase():
            with Session(self.engine) as db:
                delete_account_data(db, user)
        with ThreadPoolExecutor(max_workers=2) as pool:
            saved, erased = pool.submit(save), pool.submit(erase)
            self.assertIn(saved.result(20), (200, 404, 409))
            erased.result(20)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(User, user))
            self.assertEqual(db.scalar(select(func.count()).select_from(ChatMessage)), 0)
            self.assertIsNone(db.get(AIOperation, reserved["lease"].owner_id).request_id)

    def test_synthetic_explain_and_operational_health(self):
        self.fresh()
        result = benchmark(self.engine, self.url, self.name, self.nonce)
        self.assertEqual(result["fixtures"]["messages"], 100000)
        self.assertEqual(result["indexes_added"], [])
        with self.engine.connect() as connection:
            health = database_health(connection)
        self.assertFalse(health["revision_drift"])
        self.assertIn("storage_bytes", health)
