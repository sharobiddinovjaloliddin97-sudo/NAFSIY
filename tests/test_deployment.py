import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch, MagicMock
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

    def test_resend_email_mode_validation(self):
        resend_config = self.config(
            EMAIL_MODE="resend",
            RESEND_API_KEY="synthetic_resend_api_key_valid",
            SMTP_HOST="", SMTP_USERNAME="", SMTP_PASSWORD=""
        )
        validate_staging(resend_config)
        with self.assertRaises(ValueError):
            validate_staging(self.config(EMAIL_MODE="resend", RESEND_API_KEY=""))
        with self.assertRaises(ValueError):
            validate_staging(self.config(EMAIL_MODE="resend", RESEND_API_KEY="key", EMAIL_FROM=""))
        # onboarding@resend.dev must be rejected in staging because it is restricted to account owner only
        with self.assertRaises(ValueError):
            validate_staging(self.config(EMAIL_MODE="resend", RESEND_API_KEY="key", EMAIL_FROM="onboarding@resend.dev"))

    def test_staging_proxy_secret_validation(self):
        valid = self.config(STAGING_PROXY_SECRET="s" * 32)
        validate_staging(valid)
        invalid = self.config(STAGING_PROXY_SECRET="short_secret")
        with self.assertRaises(ValueError):
            validate_staging(invalid)

    def test_resend_delivery_sends_payload_to_api(self):
        from backend.core.email_delivery import ResendDelivery
        from backend.core.config import settings

        with patch.object(settings, "RESEND_API_KEY", "re_synthetic_key"), \
             patch.object(settings, "EMAIL_FROM", "noreply@mail.nafsiy.uz"), \
             patch.object(settings, "PUBLIC_WEB_URL", "https://staging.example.com"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock(status_code=200)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value.__enter__.return_value = mock_client

            deliv = ResendDelivery()
            deliv.send("user@example.com", "verify", "synthetic_token_123")

            mock_client.post.assert_called_once()
            args, kwargs = mock_client.post.call_args
            self.assertEqual(args[0], "https://api.resend.com/emails")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer re_synthetic_key")
            self.assertEqual(kwargs["json"]["to"], ["user@example.com"])
            self.assertIn("synthetic_token_123", kwargs["json"]["text"])

    def test_resend_delivery_negative_tests(self):
        from backend.core.email_delivery import ResendDelivery
        from backend.core.config import settings
        import httpx

        # 1. Rejects onboarding@resend.dev in staging
        with patch.object(settings, "APP_ENV", "staging"), \
             patch.object(settings, "RESEND_API_KEY", "re_synthetic"), \
             patch.object(settings, "EMAIL_FROM", "onboarding@resend.dev"):
            deliv = ResendDelivery()
            with self.assertRaises(ValueError):
                deliv.send("tester@example.com", "verify", "tok")

        # 2. Rejects unconfigured delivery
        with patch.object(settings, "RESEND_API_KEY", ""), \
             patch.object(settings, "EMAIL_FROM", ""):
            deliv = ResendDelivery()
            with self.assertRaises(ValueError):
                deliv.send("tester@example.com", "verify", "tok")

        # 3. Fails closed on upstream HTTP error without logging token
        with patch.object(settings, "RESEND_API_KEY", "re_synthetic"), \
             patch.object(settings, "EMAIL_FROM", "noreply@mail.nafsiy.uz"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock(status_code=403)
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=mock_resp)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value.__enter__.return_value = mock_client

            deliv = ResendDelivery()
            with self.assertRaises(httpx.HTTPStatusError):
                deliv.send("tester@example.com", "verify", "tok")

    def test_staging_proxy_guard_middleware_and_bypass_resistance(self):
        import asyncio
        from backend.main import app
        from backend.core.config import settings
        import httpx

        secret = "s" * 32

        async def run_probes():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                with patch.object(settings, "APP_ENV", "staging"), \
                     patch.object(settings, "STAGING_PROXY_SECRET", secret):
                    # 1. Root / is exempt without secret
                    resp = await client.get("/")
                    self.assertEqual(resp.status_code, 200)

                    # 2. Health endpoint is exempt without secret
                    resp = await client.get("/api/v1/health")
                    self.assertIn(resp.status_code, (200, 503))

                    # 3. Direct request to protected API without secret -> 403 Forbidden
                    resp = await client.get("/api/v1/quran/sources")
                    self.assertEqual(resp.status_code, 403)
                    self.assertIn("Forbidden", resp.json()["detail"])

                    # 4. Direct request with wrong secret -> 403 Forbidden
                    resp = await client.get("/api/v1/quran/sources", headers={"X-Staging-Proxy-Secret": "wrong"})
                    self.assertEqual(resp.status_code, 403)

                    # 5. Direct request with forged / short secret -> 403 Forbidden
                    resp = await client.get("/api/v1/quran/sources", headers={"X-Staging-Proxy-Secret": "short"})
                    self.assertEqual(resp.status_code, 403)

                    # 6. Alternate URL-encoded path -> 403 Forbidden
                    resp = await client.get("/%61pi/v1/quran/sources")
                    self.assertEqual(resp.status_code, 403)

                    # 7. Multiple slash path -> 403 Forbidden
                    resp = await client.get("//api/v1/quran/sources")
                    self.assertEqual(resp.status_code, 403)

                    # 8. Path traversal attempt -> 403 Forbidden
                    resp = await client.get("/api/v1/health/../quran/sources")
                    self.assertEqual(resp.status_code, 403)

                    # 9. OpenAPI docs/schema paths without secret -> 403 Forbidden
                    for doc_path in ("/docs", "/redoc", "/openapi.json"):
                        resp = await client.get(doc_path)
                        self.assertEqual(resp.status_code, 403)

                    # 10. Request with valid secret -> passes guard (status 200)
                    resp = await client.get("/api/v1/quran/sources", headers={"X-Staging-Proxy-Secret": secret})
                    self.assertEqual(resp.status_code, 200)
                    self.assertIn("sources", resp.json())

        asyncio.run(run_probes())

    def test_bootstrap_nonce_and_disposable_database_validation(self):
        # 1. Nonce regex: exactly 32 lowercase hex characters
        nonce_pattern = re.compile(r"[0-9a-f]{32}")
        valid_nonce = secrets.token_hex(16)
        self.assertEqual(len(valid_nonce), 32)
        self.assertTrue(nonce_pattern.fullmatch(valid_nonce))

        invalid_nonces = [
            "staging_bootstrap_nonce_2026",  # non-hex
            "short_nonce",                   # too short
            valid_nonce.upper(),             # uppercase
            valid_nonce + "0",               # 33 chars
            "",                              # empty
            "g" * 32,                        # 'g' is not hex
        ]
        for bad in invalid_nonces:
            with self.subTest(bad_nonce=bad):
                self.assertIsNone(nonce_pattern.fullmatch(bad))

        # 2. Database name regex: nafsiy_disposable_[a-z0-9_]{8,48}
        dbname_pattern = re.compile(r"nafsiy_disposable_[a-z0-9_]{8,48}")
        valid_dbnames = [
            "nafsiy_disposable_staging01",
            "nafsiy_disposable_test_12345678",
            "nafsiy_disposable_" + "a" * 48,
        ]
        for name in valid_dbnames:
            with self.subTest(valid_name=name):
                self.assertTrue(dbname_pattern.fullmatch(name))

        invalid_dbnames = [
            "nafsiy_staging",               # missing disposable prefix
            "nafsiy_primary",               # primary target
            "nafsiy_disposable_short",      # suffix 'short' is only 5 chars (< 8)
            "nafsiy_disposable_Staging01",  # capital letter
            "nafsiy_disposable_staging-01", # hyphen is not allowed in [a-z0-9_]
            "",                             # empty
        ]
        for bad in invalid_dbnames:
            with self.subTest(bad_name=bad):
                self.assertIsNone(dbname_pattern.fullmatch(bad))

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
