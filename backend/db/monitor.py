"""Access-controlled operator CLI consumes these aggregates; no public route."""
from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy import text

from backend.core.config import settings
from backend.db.backups import verify_backup
from backend.db.readiness import head, revision


def pool_status(engine):
    pool = engine.pool
    if not hasattr(pool, "checkedout"):
        return {"type": type(pool).__name__, "process_local": True}
    overflow_limit = getattr(pool, "_max_overflow", None)
    limit = pool.size() + overflow_limit if overflow_limit is not None and overflow_limit >= 0 else None
    return {"type": type(pool).__name__, "size": pool.size(), "checked_out": pool.checkedout(),
            "overflow": max(0, pool.overflow()), "limit": limit,
            "process_local": True}


def backup_health(directory):
    value = {"present": False, "integrity": False, "restore_test_success": False, "offsite_encrypted": False}
    if directory is None:
        return value
    try:
        metadata = verify_backup(directory)
        now = datetime.now(timezone.utc)
        receipts = []
        for path in Path(directory).glob("restore-*.json"):
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry.get("success") and entry.get("dump_sha256") == metadata["dump_sha256"]:
                receipts.append(entry)
        offsite = Path(directory) / "offsite-receipt.json"
        if offsite.exists():
            entry = json.loads(offsite.read_text(encoding="utf-8"))
            value["offsite_encrypted"] = bool(entry.get("offsite_encrypted") and entry.get("integrity_checked")
                                             and entry.get("dump_sha256") == metadata["dump_sha256"])
            value["offsite_receipt_is_local_attestation"] = True
        latest = max(receipts, key=lambda row: row["verified_at_utc"]) if receipts else None
        value.update(present=True, integrity=True,
                     age_hours=max(0, (now - datetime.fromisoformat(metadata["created_at_utc"])).total_seconds() / 3600),
                     restore_test_success=latest is not None,
                     restore_test_age_hours=max(0, (now - datetime.fromisoformat(latest["verified_at_utc"])).total_seconds() / 3600) if latest else None)
    except Exception:
        value["check_failed"] = True
    return value


def database_health(connection, backup_directory=None):
    current = revision(connection)
    values = {"connected": True, "revision": current, "head": head(), "revision_drift": current != head(),
              "storage_bytes": connection.scalar(text("SELECT pg_database_size(current_database())")),
              "connections": connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()")),
              "max_connections": int(connection.scalar(text("SHOW max_connections"))),
              "backup": backup_health(backup_directory)}
    if current == head():
        values.update(expired_capacity_backlog=connection.scalar(text("""
          SELECT count(*) FROM ai_operations o JOIN ai_global_slots s ON s.slot=o.slot
          WHERE o.status='active' AND (s.expires_at<=clock_timestamp() OR s.owner<>o.id OR s.version<>o.slot_version)
          """)), expired_chat_leases=connection.scalar(text("SELECT count(*) FROM chat_generation_leases WHERE expires_at<=clock_timestamp()")),
          idempotency_retention_backlog=connection.scalar(text("""
          SELECT count(*) FROM ai_idempotency i WHERE i.updated_at<clock_timestamp()-interval '30 days'
          AND (i.response_json IS NULL OR i.response_json<>'{"expired": true}')
          AND NOT EXISTS (SELECT 1 FROM chat_generation_leases l WHERE l.session_id=i.session_id AND l.expires_at>clock_timestamp())
          """)), expired_ai_rate_buckets=connection.scalar(text("SELECT count(*) FROM ai_rate_limits WHERE created_at<clock_timestamp()-interval '10 minutes'")),
          expired_auth_buckets=connection.scalar(text("SELECT count(*) FROM auth_rate_limits WHERE expires_at<=clock_timestamp()")))
    return values
