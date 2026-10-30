"""Direct-connection logical backups. Dumps/manifests are private, never logged."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from sqlalchemy import inspect, text

from backend.db.readiness import (GATE, ReadinessError, assert_disposable, assert_empty,
                                  preflight, revision, schema_hash)


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def private_directory(path):
    path = Path(path).resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if os.name == "nt":
        owner = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", owner + ":(OI)(CI)F"], capture_output=True)
        if result.returncode:
            raise ReadinessError("Cannot restrict backup directory ACL")
    else:
        path.chmod(0o700)
    return path


def client_environment(url):
    # Credentials are passed only to a short-lived child environment, never argv.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGHOST=url.host, PGPORT=str(url.port or 5432), PGUSER=url.username or "",
               PGPASSWORD=url.password or "", PGDATABASE=url.database, PGCONNECT_TIMEOUT="10")
    for key, variable in (("sslmode", "PGSSLMODE"), ("channel_binding", "PGCHANNELBINDING"), ("sslrootcert", "PGSSLROOTCERT")):
        if key in url.query:
            env[variable] = str(url.query[key])
    return env


def run_client(executable, args, url=None, timeout=600):
    try:
        result = subprocess.run([str(executable), *map(str, args)], env=client_environment(url) if url else None,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ReadinessError("PostgreSQL client unavailable or timed out") from None
    if result.returncode:
        raise ReadinessError("PostgreSQL client operation failed; target is not approved for reuse")
    return result.stdout


def client_major(directory, client):
    output = run_client(Path(directory) / (client + (".exe" if os.name == "nt" else "")), ["--version"]).decode()
    found = re.search(r"PostgreSQL\) (\d+)\.", output)
    if not found:
        raise ReadinessError("Unrecognized PostgreSQL client version")
    return int(found[1])


def executable(directory, client):
    return Path(directory) / (client + (".exe" if os.name == "nt" else ""))


def table_fingerprints(connection):
    result = {}
    inspector = inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    for table in sorted(inspector.get_table_names(schema="public")):
        keys = inspector.get_pk_constraint(table, schema="public")["constrained_columns"]
        if not keys:
            raise ReadinessError("Backup verification requires primary keys on every table")
        order = ", ".join(quote(key) for key in keys)
        query = text(f"SELECT row_to_json(t)::text FROM public.{quote(table)} AS t ORDER BY {order}")
        checksum, count = hashlib.sha256(), 0
        # Hash canonical JSON records from one exported snapshot, not a later view.
        rows = connection.execution_options(stream_results=True).execute(query)
        for row in rows:
            canonical = json.dumps(json.loads(row[0], parse_float=Decimal), sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=True, default=str)
            checksum.update(canonical.encode() + b"\n")
            count += 1
        result[table] = {"rows": count, "sha256": checksum.hexdigest()}
    return result


def sequence_observations(connection):
    quote = connection.dialect.identifier_preparer.quote
    names = connection.execute(text("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")).scalars().all()
    return {name: dict(connection.execute(text(f"SELECT last_value,is_called FROM public.{quote(name)}")).mappings().one()) for name in names}


def write_manifest(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    if os.name != "nt":
        Path(path).chmod(0o600)


def create_backup(engine, url, destination, pg_bin):
    if "-pooler" in (url.host or ""):
        raise ReadinessError("Backups require a direct connection")
    directory = private_directory(destination)
    dump = directory / "database.dump"
    started = datetime.now(timezone.utc)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            version = int(connection.scalar(text("SHOW server_version_num")))
            if client_major(pg_bin, "pg_dump") != version // 10000 or client_major(pg_bin, "pg_restore") != version // 10000:
                raise ReadinessError("Use pg_dump/pg_restore matching the server major version")
            checks = preflight(connection)
            if not checks["schema_compatible_at_revision"] or checks["unexpected_schemas"]:
                raise ReadinessError("Schema drift or unknown revision blocks backup workflow")
            snapshot = connection.scalar(text("SELECT pg_export_snapshot()"))
            metadata = {"format_version": 2, "row_hash_format": "canonical-json-exact-decimals-v1", "created_at_utc": started.isoformat(),
                        "postgres_version_num": version, "client_major": version // 10000,
                        "alembic_revision": revision(connection), "schema_sha256": schema_hash(connection),
                        "snapshot": {"isolation": "repeatable read read only", "exported_snapshot": snapshot,
                                     "transaction_snapshot": connection.scalar(text("SELECT pg_current_snapshot()::text"))},
                        "sequences": sequence_observations(connection),
                        "tables": table_fingerprints(connection), "offsite_encrypted": False,
                        "source_fingerprint": hashlib.sha256(f"{url.host}:{url.port or 5432}/{url.database}".encode()).hexdigest()}
            run_client(executable(pg_bin, "pg_dump"), ["--format=custom", "--no-owner", "--no-acl",
                "--lock-wait-timeout=5000", "--snapshot", snapshot, "--file", dump], url)
    run_client(executable(pg_bin, "pg_restore"), ["--list", dump])
    metadata.update(dump_file="database.dump", dump_bytes=dump.stat().st_size, dump_sha256=digest_file(dump))
    write_manifest(directory / "manifest.json", metadata)
    return {"backup_created": True, "dump_bytes": metadata["dump_bytes"], "revision": metadata["alembic_revision"],
            "offsite_encrypted": False, "restore_verified": False}


def verify_backup(directory, pg_bin=None):
    directory = Path(directory)
    metadata = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (metadata.get("format_version") != 2 or metadata.get("dump_file") != "database.dump"
            or metadata.get("row_hash_format") != "canonical-json-exact-decimals-v1"):
        raise ReadinessError("Unknown backup manifest format")
    from backend.db.readiness import SCHEMAS
    reviewed = json.loads(SCHEMAS.read_text(encoding="utf-8"))
    if (metadata.get("schema_sha256") != reviewed.get(metadata.get("alembic_revision") or "unversioned_baseline")
            or not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("dump_sha256", "")))
            or type(metadata.get("dump_bytes")) is not int or metadata["dump_bytes"] <= 0
            or not isinstance(metadata.get("tables"), dict) or not isinstance(metadata.get("sequences"), dict)):
        raise ReadinessError("Backup manifest does not describe a reviewed application schema")
    dump = directory / "database.dump"
    if dump.stat().st_size != metadata["dump_bytes"] or digest_file(dump) != metadata["dump_sha256"]:
        raise ReadinessError("Backup integrity verification failed")
    if pg_bin is not None:
        if client_major(pg_bin, "pg_restore") != metadata["client_major"]:
            raise ReadinessError("Restore client version does not match backup")
        run_client(executable(pg_bin, "pg_restore"), ["--list", dump])
    return metadata


def restore_backup(engine, url, confirmation, nonce, directory, pg_bin):
    metadata = verify_backup(directory, pg_bin)
    start = time.monotonic()
    with engine.connect() as control:
        control.execute(text("SELECT pg_advisory_lock(:key)"), {"key": GATE})
        try:
            assert_disposable(control, url, confirmation, nonce)
            assert_empty(control)
            if int(control.scalar(text("SHOW server_version_num"))) // 10000 != metadata["client_major"]:
                raise ReadinessError("Restore rehearsal requires matching server major versions")
            control.commit()  # Session advisory lock remains; no transaction while client runs.
            run_client(executable(pg_bin, "pg_restore"), ["--no-owner", "--no-acl", "--exit-on-error",
                "--single-transaction", "--dbname", url.database, Path(directory) / "database.dump"], url)
            with control.begin():
                control.exec_driver_sql("SET TRANSACTION READ ONLY")
                control.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                matched = (revision(control) == metadata["alembic_revision"]
                           and schema_hash(control) == metadata["schema_sha256"]
                           and table_fingerprints(control) == metadata["tables"])
                sequences = sequence_observations(control)
                matched = matched and set(sequences) == set(metadata["sequences"]) and all(
                    sequences[name]["last_value"] >= value["last_value"] and
                    (sequences[name]["last_value"] > value["last_value"] or not value["is_called"] or sequences[name]["is_called"])
                    for name, value in metadata["sequences"].items())
                if not matched:
                    raise ReadinessError("Restored verification failed; quarantine target, never overwrite it")
            receipt = {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "success": True,
                       "ready_for_application": False,
                       "dump_sha256": metadata["dump_sha256"], "revision": metadata["alembic_revision"],
                       "duration_seconds": round(time.monotonic() - start, 3)}
            # Receipt is tied to the exact dump and separately retained with it.
            write_manifest(Path(directory) / ("restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"), receipt)
            return receipt
        finally:
            control.rollback()
            control.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": GATE})
            control.commit()
