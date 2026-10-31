import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.core.config import Settings
from backend.core.deployment import validate_staging
from backend.db.offsite import restic, upload
from backend.db.readiness import ReadinessError


class DeploymentTests(unittest.TestCase):
    def test_origin_credentials_and_invalid_ports_rejected_even_with_matching_lists(self):
        for origin in ("https://:password@staging.example.invalid", "https://staging.example.invalid:invalid"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                validate_staging(self.config(PUBLIC_WEB_URL=origin, AUTH_TRUSTED_ORIGINS=[origin],
                                             CORS_ORIGINS=[origin]))

    def config(self, **values):
        defaults = dict(_env_file=None, APP_ENV="staging", DATABASE_URL="postgresql+psycopg://runtime:synthetic@staging.invalid/db?sslmode=require",
            PUBLIC_WEB_URL="https://staging.example.invalid", AUTH_TRUSTED_ORIGINS=["https://staging.example.invalid"],
            CORS_ORIGINS=["https://staging.example.invalid"], AUTH_COOKIE_SECURE=True, JWT_SECRET_KEY="x"*64,
            EMAIL_MODE="smtp", EMAIL_FROM="security@example.invalid", SMTP_HOST="smtp.example.invalid",
            SMTP_USERNAME="synthetic", SMTP_PASSWORD="synthetic", GROQ_API_KEY="synthetic",
            REQUIRE_EMAIL_VERIFICATION=True, MAX_GLOBAL_AI_GENERATIONS=2)
        return Settings(**(defaults | values))

    def test_staging_configuration_fails_closed(self):
        validate_staging(self.config())
        for values in (dict(APP_ENV="production"), dict(AUTH_COOKIE_SECURE=False), dict(JWT_SECRET_KEY="short"),
                       dict(EMAIL_MODE="disabled"), dict(REQUIRE_EMAIL_VERIFICATION=False), dict(ACCOUNT_DELETION_ENABLED=True),
                       dict(CORS_ORIGINS=["*"]), dict(AUTH_TRUSTED_ORIGINS=["http://localhost:3000"]),
                       dict(PUBLIC_WEB_URL="http://staging.example.invalid"), dict(SMTP_PASSWORD=""),
                       dict(PUBLIC_WEB_URL="https://:password@staging.example.invalid"),
                       dict(PUBLIC_WEB_URL="https://staging.example.invalid:invalid"),
                       dict(DATABASE_URL="postgresql+psycopg://runtime:synthetic@staging.invalid/db"),
                       dict(MAX_GLOBAL_AI_GENERATIONS=3)):
            with self.subTest(names=list(values)), self.assertRaises(ValueError):
                validate_staging(self.config(**values))

    def test_offsite_failures_are_sanitized_and_no_receipt_is_created(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ReadinessError):
            restic(["check"])
        with tempfile.TemporaryDirectory() as root, patch("backend.db.offsite.verify_backup", return_value={}), \
                patch("backend.db.offsite.restic", side_effect=ReadinessError("failed")):
            with self.assertRaises(ReadinessError): upload(root)
            self.assertFalse((Path(root)/"offsite-receipt.json").exists())

    def test_offsite_receipt_only_after_repository_integrity_check(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/"database.dump").write_bytes(b"synthetic")
            with patch("backend.db.offsite.verify_backup", return_value={}), patch("backend.db.offsite.restic", side_effect=[
                b'{"message_type":"summary","snapshot_id":"synthetic"}\n', b'']) as runner:
                result=upload(root)
            self.assertTrue(result["offsite_encrypted"])
            self.assertFalse(result["restore_verified"])
            self.assertEqual(runner.call_args_list[-1].args[0], ["check", "--read-data"])

    def test_migration_history_preserved(self):
        import subprocess
        for file in Path("migrations/versions").glob("*.py"):
            if file.name.startswith("e6f1027a93bd"): continue
            original=subprocess.check_output(["git","show",f"HEAD:{file.as_posix()}"])
            self.assertEqual(original.replace(b"\r\n",b"\n"),file.read_bytes().replace(b"\r\n",b"\n"))
