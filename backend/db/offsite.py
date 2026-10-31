"""Operator-only encrypted offsite adapter. Requires an existing initialized restic repo.
No repository creation, pruning, restore activation or secrets in argv/logs.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from backend.db.backups import verify_backup, digest_file
from backend.db.readiness import ReadinessError


def restic(args):
    if not os.environ.get("RESTIC_REPOSITORY") or not os.environ.get("RESTIC_PASSWORD_FILE"):
        raise ReadinessError("Offsite repository and password file are required")
    try:
        result = subprocess.run(["restic", *args], capture_output=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired):
        raise ReadinessError("Offsite tool unavailable or timed out") from None
    if result.returncode:
        raise ReadinessError("Offsite operation failed")
    return result.stdout


def upload(directory):
    directory = Path(directory).resolve()
    verify_backup(directory)
    output = restic(["backup", "--json", "--tag", "nafsiy", str(directory)])
    summaries = [json.loads(line) for line in output.splitlines() if line.strip()]
    snapshot = next((row.get("snapshot_id") for row in summaries if row.get("message_type") == "summary"), None)
    if not snapshot:
        raise ReadinessError("Offsite snapshot receipt missing")
    # Full encrypted repository integrity read; success is not a database restore proof.
    restic(["check", "--read-data"])
    receipt = {"offsite_encrypted": True, "integrity_checked": True, "snapshot_id": snapshot,
               "dump_sha256": digest_file(directory / "database.dump"),
               "checked_at": datetime.now(timezone.utc).isoformat(), "restore_verified": False}
    target = directory / "offsite-receipt.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True)
    return receipt


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        upload(args.directory)
        print(json.dumps({"event": "backup_offsite_verified", "restore_verified": False}))
    except Exception:
        print(json.dumps({"event": "backup_offsite_failed", "alert": True}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
