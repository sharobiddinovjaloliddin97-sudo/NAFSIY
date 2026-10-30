"""python -m backend.db.operations --help. Errors omit credentials and SQL data."""
import argparse
import json
import os
from pathlib import Path

from backend.db.backups import create_backup, restore_backup, verify_backup
from backend.db.monitor import database_health, pool_status
from backend.db.readiness import (ReadinessError, bootstrap, configured_url, engine_for, preflight,
                                  upgrade_disposable, verify_artifacts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "bootstrap", "upgrade-disposable", "backup", "verify-backup", "restore", "monitor"))
    parser.add_argument("--database", choices=("test", "primary", "disposable"), default="test")
    parser.add_argument("--confirm-database")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--pg-bin", type=Path)
    parser.add_argument("--revision", default="head")
    args = parser.parse_args()
    try:
        verify_artifacts()
        if args.action == "verify-backup":
            verify_backup(args.directory, args.pg_bin)
            print(json.dumps({"integrity": True}))
            return
        if args.action in ("bootstrap", "restore", "upgrade-disposable") and args.database != "disposable":
            raise ReadinessError("Mutating operator commands only accept registered disposable targets")
        url = configured_url(args.database)
        engine = engine_for(url)
        try:
            if args.action == "bootstrap":
                result = {"revision": bootstrap(engine, url, args.confirm_database, os.getenv("NAFSIY_DISPOSABLE_NONCE"), args.revision)}
            elif args.action == "upgrade-disposable":
                result = {"revision": upgrade_disposable(engine, url, args.confirm_database, os.getenv("NAFSIY_DISPOSABLE_NONCE"))}
            elif args.action == "backup":
                result = create_backup(engine, url, args.directory, args.pg_bin)
            elif args.action == "restore":
                result = restore_backup(engine, url, args.confirm_database, os.getenv("NAFSIY_DISPOSABLE_NONCE"), args.directory, args.pg_bin)
            else:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    result = preflight(connection) if args.action == "preflight" else database_health(connection, args.directory)
                    if args.action == "monitor":
                        result["cli_pool"] = pool_status(engine)
            print(json.dumps(result, sort_keys=True, default=str))
            if args.action == "preflight" and (not result["schema_compatible_at_revision"] or not result["supported_server"]
                                               or not all(result["privileges"].values())
                                               or (result["revision"] == result["head"] and not result["models_match"])):
                raise SystemExit(1)
        finally:
            engine.dispose()
    except ReadinessError as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}))
        raise SystemExit(1) from None
    except Exception as error:
        print(json.dumps({"status": "unavailable", "category": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
