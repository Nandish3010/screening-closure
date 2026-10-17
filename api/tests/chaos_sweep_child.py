"""Child process for the kill-mid-sweep chaos test (F4): runs one scheduler sweep against a database
and exits. The parent kills it (SIGKILL, whole process group) part-way through.

    python chaos_sweep_child.py DATABASE_URL SWEEP_NOW_ISO WORKERS

WORKERS 1 runs `run_sweep` in this process; N > 1 runs `run_sweep_parallel` (N spawned worker
processes, as the scheduler route does). Prints `done <emitted>` on a clean finish. Test-only: refuses
any database whose name does not end in `_test`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.calendar import default_calendar  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import create_pool  # noqa: E402
from app.pathway import load_pathway  # noqa: E402
from app.store_pg import PostgresEventStore  # noqa: E402
from app.sweep import run_sweep, run_sweep_parallel  # noqa: E402
from testdb import assert_test_database  # noqa: E402


def main() -> int:
    url, now_iso, workers = sys.argv[1], sys.argv[2], int(sys.argv[3])
    assert_test_database(url)
    now = datetime.fromisoformat(now_iso)
    pathway = load_pathway(settings.pathway_path)
    if workers > 1:
        result = run_sweep_parallel(url, pathway, default_calendar(), now, workers=workers)
    else:
        pool = create_pool(url, max_size=2)
        try:
            result = run_sweep(PostgresEventStore(pool), pathway, default_calendar(), now)
        finally:
            pool.close()
    print(f"done {result.mark_overdue + result.escalate + result.handoff_bounced} errors={len(result.errors)}", flush=True)
    return 0 if not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
