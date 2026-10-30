"""Local-only test provisioning. Never creates/drops databases on Neon."""
from contextlib import contextmanager
import os
from pathlib import Path
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from backend.db.readiness import ReadinessError, configured_url, identity


def local_admin():
    url = make_url(os.environ["NAFSIY_LOCAL_PG_ADMIN_URL"])
    if url.host not in ("127.0.0.1", "localhost") or url.database != "postgres":
        raise ReadinessError("Disposable test provisioning requires the explicit local cluster")
    engine = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        expected = Path(os.environ["NAFSIY_LOCAL_PG_DATA"]).resolve()
        actual = Path(connection.scalar(text("SHOW data_directory"))).resolve()
        if actual != expected or expected != (Path(__file__).resolve().parents[1] / "tmp/database-readiness/cluster").resolve():
            engine.dispose()
            raise ReadinessError("Local cluster directory ownership verification failed")
    return engine, url


@contextmanager
def disposable_database():
    admin, admin_url = local_admin()
    name, nonce = "nafsiy_disposable_" + uuid.uuid4().hex, uuid.uuid4().hex
    url = admin_url.set(database=name)
    for target in ("primary", "test"):
        if identity(url) == identity(configured_url(target)):
            raise ReadinessError("Refusing production/shared-test provisioning")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
        connection.exec_driver_sql(f'COMMENT ON DATABASE "{name}" IS \'nafsiy-disposable:{nonce}\'')
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        yield engine, url, name, nonce
    finally:
        engine.dispose()
        with admin.connect() as connection:
            marker = connection.scalar(text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=:name"), {"name": name})
            if marker != "nafsiy-disposable:" + nonce:
                raise ReadinessError("Disposable drop marker mismatch; database retained for inspection")
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
