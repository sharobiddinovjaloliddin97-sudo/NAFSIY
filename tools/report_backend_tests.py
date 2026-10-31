"""Machine-readable full-suite evidence, with explicit fail/error/skip totals."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed, errors, skipped = len(result.failures), len(result.errors), len(result.skipped)
    evidence = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "run": result.testsRun,
        "passed": result.testsRun - failed - errors - skipped - len(result.expectedFailures) - len(result.unexpectedSuccesses),
        "failed": failed, "errors": errors, "skipped": skipped,
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "scope": "owned isolated PostgreSQL and synthetic local fixtures; no primary Neon",
    }
    (ROOT / "evaluations/backend_regression_results.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence))
    return int(not result.wasSuccessful() or skipped != 0)


if __name__ == "__main__":
    raise SystemExit(main())
