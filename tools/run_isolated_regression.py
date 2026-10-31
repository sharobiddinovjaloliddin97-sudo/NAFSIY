"""Run all integration tests on a nonce-owned local database; never migrate Neon."""
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from disposable_postgres import disposable_database
from backend.db.readiness import bootstrap


def main():
    with disposable_database() as (engine, url, name, nonce):
        bootstrap(engine, url, name, nonce)
        env = dict(os.environ, TEST_DATABASE_URL=url.render_as_string(hide_password=False),
                   NAFSIY_RUN_POSTGRES_TESTS="1", NAFSIY_RUN_DISPOSABLE_TESTS="1")
        return subprocess.call([sys.executable, "tools/report_backend_tests.py"], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
