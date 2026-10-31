"""Scheduled runner: explicit staging source, local verified dump, encrypted upload.
Install PostgreSQL 18 clients and restic on a private operator runner. No provisioning.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import make_url
from backend.db.readiness import configured_url, identity, engine_for, ReadinessError
from backend.db.backups import create_backup
from backend.db.offsite import upload


def main():
    try:
        source = make_url(os.environ["BACKUP_DATABASE_URL"])
        if source.get_backend_name() != "postgresql" or source.query.get("sslmode") not in ("require", "verify-full"):
            raise ReadinessError("Staging backup requires TLS PostgreSQL")
        if any(identity(source) == identity(configured_url(target)) for target in ("primary", "test")):
            raise ReadinessError("Staging runner refuses primary/shared test source")
        directory = Path(os.environ["BACKUP_DIRECTORY"]) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        engine = engine_for(source.set(drivername="postgresql+psycopg"))
        try:
            create_backup(engine, source, directory, Path(os.environ["PG_BIN"]))
        finally:
            engine.dispose()
        upload(directory)
        print(json.dumps({"event": "scheduled_backup_success", "restore_verified": False}))
    except Exception:
        print(json.dumps({"event": "scheduled_backup_failed", "alert": True}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
