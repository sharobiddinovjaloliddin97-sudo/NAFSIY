"""Operator-only database checks/bootstrap. Never prints connection strings/data."""
import hashlib
import json
import os
from pathlib import Path
import re

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from backend.db.catalog import Base

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "migrations" / "bootstrap" / "baseline.sql"
MANIFEST = SNAPSHOT.with_name("integrity.json")
SCHEMAS = SNAPSHOT.with_name("schema_fingerprints.json")
BASELINE = "69e1d999085f"
GATE = 906196


class ReadinessError(RuntimeError):
    """Fixed, privacy-safe operator error. Never wrap a raw DB error message."""


def config(connection=None):
    value = Config(str(ROOT / "alembic.ini"))
    value.set_main_option("script_location", str(ROOT / "migrations"))
    if connection is not None:
        value.attributes["connection"] = connection
    return value


def head():
    return ScriptDirectory.from_config(config()).get_current_head()


def configured_url(target):
    keys = {"test": "TEST_DATABASE_URL", "primary": "DATABASE_URL", "disposable": "NAFSIY_DISPOSABLE_DATABASE_URL"}
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    value = values.get(keys[target])
    if not value:
        raise ReadinessError("Selected database configuration is missing")
    url = make_url(value)
    if url.get_backend_name() != "postgresql" or not url.host or not url.database:
        raise ReadinessError("An explicit PostgreSQL database is required")
    if "-pooler" in url.host:
        raise ReadinessError("Operator workflows require a direct connection")
    if target != "primary":
        primary = values.get("DATABASE_URL")
        if not primary or identity(url) == identity(make_url(primary)):
            raise ReadinessError("Selected target must differ from primary")
    return url.set(drivername="postgresql+psycopg")


def identity(url):
    return ((url.host or "").lower().replace("-pooler", ""), url.port or 5432, url.database)


def engine_for(url):
    return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})


def lf_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def verify_artifacts():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data["baseline_revision"] != BASELINE or lf_hash(SNAPSHOT) != data["snapshot_sha256_lf"]:
        raise ReadinessError("Bootstrap snapshot integrity check failed")
    if {path.name for path in (ROOT / "migrations" / "versions").glob("*.py")} != set(data["migration_sha256_lf"]):
        raise ReadinessError("Migration inventory changed; reviewed artifacts must be updated")
    for name, expected in data["migration_sha256_lf"].items():
        if Path(name).name != name or lf_hash(ROOT / "migrations" / "versions" / name) != expected:
            raise ReadinessError("Historical migration integrity check failed")
    if "schema_fingerprints_sha256_lf" in data and lf_hash(SCHEMAS) != data["schema_fingerprints_sha256_lf"]:
        raise ReadinessError("Reviewed schema fingerprint integrity check failed")
    return data


def assert_disposable(connection, url, confirmation, nonce):
    actual = connection.scalar(text("SELECT current_database()"))
    if not re.fullmatch(r"nafsiy_disposable_[a-z0-9_]{8,48}", actual or "") or actual != confirmation or actual != url.database:
        raise ReadinessError("An explicitly confirmed disposable database is required")
    if not nonce or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ReadinessError("Disposable ownership nonce is required")
    for target in ("primary", "test"):
        if identity(url) == identity(configured_url(target)):
            raise ReadinessError("Primary and shared test databases cannot be destructive targets")
    label = connection.scalar(text("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"))
    if label != "nafsiy-disposable:" + nonce:
        raise ReadinessError("Disposable database ownership marker does not match")
    others = connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()"))
    if others:
        raise ReadinessError("Disposable target has other connections")


def object_counts(connection):
    return dict(connection.execute(text("""
        SELECT 'relations', count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
        UNION ALL SELECT 'routines', count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
        UNION ALL SELECT 'types', count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
          WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
        UNION ALL SELECT 'schemas', count(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('information_schema','public')
        UNION ALL SELECT 'extensions', count(*) FROM pg_extension WHERE extname <> 'plpgsql'
        UNION ALL SELECT 'publications', count(*) FROM pg_publication
        UNION ALL SELECT 'event_triggers', count(*) FROM pg_event_trigger
        """)).all())


def assert_empty(connection):
    if any(object_counts(connection).values()):
        raise ReadinessError("Target is not empty; no bootstrap or restore was performed")


def normalized(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_check(expression):
    # PG16 casts the whole text array; PG18 casts each varchar element. Strip
    # only equivalent text/varchar coercions. Column types are checked separately.
    return re.sub(r"::(?:character varying|text)(?:\[\])?", "", normalized(expression))


def schema_state(connection):
    inspector = inspect(connection)
    result = {"__catalog__": object_counts(connection)}
    for name in sorted(inspector.get_table_names(schema="public")):
        result[name] = {
            "columns": [{"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"], "default": normalized(c.get("default"))}
                        for c in inspector.get_columns(name, schema="public")],
            "primary_key": inspector.get_pk_constraint(name, schema="public"),
            "foreign_keys": sorted(inspector.get_foreign_keys(name, schema="public"), key=lambda row: row["name"] or ""),
            "unique": sorted(inspector.get_unique_constraints(name, schema="public"), key=lambda row: row["name"] or ""),
            "checks": sorted([{**row, "sqltext": normalized_check(row["sqltext"])} for row in inspector.get_check_constraints(name, schema="public")], key=lambda row: row["name"] or ""),
            "indexes": sorted(inspector.get_indexes(name, schema="public"), key=lambda row: row["name"] or ""),
        }
    return result


def schema_hash(connection):
    return hashlib.sha256(json.dumps(schema_state(connection), sort_keys=True, default=str).encode()).hexdigest()


def schema_matches_models(connection):
    context = MigrationContext.configure(connection, opts={"compare_type": True, "target_metadata": Base.metadata})
    return not compare_metadata(context, Base.metadata)


def revision(connection):
    if not inspect(connection).has_table("alembic_version", schema="public"):
        return None
    rows = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
    if len(rows) != 1:
        raise ReadinessError("Expected one known Alembic revision")
    return rows[0]


def preflight(connection):
    verify_artifacts()
    current = revision(connection)
    scripts = ScriptDirectory.from_config(config())
    known = {item.revision for item in scripts.walk_revisions()}
    if current not in known and current is not None:
        raise ReadinessError("Unknown Alembic revision; manual review required")
    expected = json.loads(SCHEMAS.read_text(encoding="utf-8")) if SCHEMAS.exists() else {}
    actual_tables = set(inspect(connection).get_table_names(schema="public"))
    key = current or "unversioned_baseline"
    compatible = key in expected and schema_hash(connection) == expected[key]
    privileges = dict(connection.execute(text("""SELECT 'connect', has_database_privilege(current_database(), 'CONNECT')
      UNION ALL SELECT 'schema_usage', has_schema_privilege('public','USAGE')
      UNION ALL SELECT 'schema_create', has_schema_privilege('public','CREATE')
      UNION ALL SELECT 'table_dml', coalesce(bool_and(has_table_privilege(c.oid,'SELECT') AND has_table_privilege(c.oid,'INSERT')
        AND has_table_privilege(c.oid,'UPDATE') AND has_table_privilege(c.oid,'DELETE')),true)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'
      UNION ALL SELECT 'owns_tables', coalesce(bool_and(pg_has_role(c.relowner,'USAGE')),true)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'
      UNION ALL SELECT 'sequence_usage', coalesce(bool_and(has_sequence_privilege(c.oid,'USAGE,SELECT')),true)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S'
      """)).all())
    version = int(connection.scalar(text("SHOW server_version_num")))
    counts = object_counts(connection)
    pending = [item.revision for item in reversed(list(scripts.iterate_revisions(head(), current or "base")))]
    return {"connected": True, "server_version_num": version, "supported_server": 160000 <= version < 190000,
            "revision": current, "head": head(), "pending_migrations": pending,
            "requires_baseline_adoption": current is None,
            "schema_compatible_at_revision": compatible,
            "models_match": schema_matches_models(connection) if current == head() else False,
            "unexpected_tables": len(actual_tables - set(Base.metadata.tables) - {"alembic_version"}),
            "unexpected_schemas": counts["schemas"], "privileges": privileges,
            "required_extensions": [], "installed_extensions": connection.execute(text("SELECT extname FROM pg_extension ORDER BY extname")).scalars().all()}


def bootstrap(engine, url, confirmation, nonce, target_revision="head"):
    verify_artifacts()
    if target_revision != "head" and target_revision not in {item.revision for item in ScriptDirectory.from_config(config()).walk_revisions()}:
        raise ReadinessError("Bootstrap requires an exact reviewed revision or head")
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": GATE})
        assert_disposable(connection, url, confirmation, nonce)
        assert_empty(connection)
        connection.exec_driver_sql("SET LOCAL search_path TO public")
        connection.exec_driver_sql(SNAPSHOT.read_text(encoding="utf-8"))
        command.upgrade(config(connection), target_revision)
        current = revision(connection)
        if current == head() and not schema_matches_models(connection):
            raise ReadinessError("Fresh schema differs from application models")
        if SCHEMAS.exists():
            expected = json.loads(SCHEMAS.read_text(encoding="utf-8"))
            if current in expected and schema_hash(connection) != expected[current]:
                raise ReadinessError("Fresh schema fingerprint does not match reviewed schema")
    return current


def upgrade_disposable(engine, url, confirmation, nonce):
    verify_artifacts()
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": GATE})
        assert_disposable(connection, url, confirmation, nonce)
        before = preflight(connection)
        if not before["schema_compatible_at_revision"]:
            raise ReadinessError("Existing schema drift blocks upgrade")
        command.upgrade(config(connection), "head")
        if not schema_matches_models(connection):
            raise ReadinessError("Upgraded schema differs from models")
        if schema_hash(connection) != json.loads(SCHEMAS.read_text(encoding="utf-8"))[head()]:
            raise ReadinessError("Upgraded constraints/indexes differ from reviewed schema")
    return head()
