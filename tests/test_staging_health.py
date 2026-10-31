"""Health/revision probes use only an in-memory SQLite engine; never Neon."""
import unittest
from unittest.mock import patch
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from backend import main
from backend.db.readiness import head


class StagingHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_rejects_missing_or_wrong_revision(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        try:
            with patch.object(main, "engine", engine), patch.object(main.settings, "APP_ENV", "staging"):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as client:
                    self.assertEqual((await client.get("/api/v1/health")).status_code, 503)
                    with engine.begin() as connection:
                        connection.execute(text("CREATE TABLE alembic_version(version_num text)"))
                        connection.execute(text("INSERT INTO alembic_version VALUES('wrong')"))
                    response = await client.get("/api/v1/health")
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn("wrong", response.text)
                    with engine.begin() as connection:
                        connection.execute(text("UPDATE alembic_version SET version_num=:head"), {"head": head()})
                    response = await client.get("/api/v1/health")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("max-age=", response.headers["Strict-Transport-Security"])
        finally:
            engine.dispose()
