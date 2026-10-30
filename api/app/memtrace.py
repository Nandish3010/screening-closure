"""Opt-in memory diagnosis (M7-8): `MEMTRACE=1` starts `tracemalloc` and installs a SIGUSR1 handler that writes where memory is held.

Python's own tracer, no extra package. The first signal records a baseline snapshot; each later signal writes the allocation sites
that grew since the baseline, largest first, to `MEMTRACE_OUT` (default /tmp/memtrace.txt). Off by default (it slows the process by a
few times and adds memory), so it is for one pod at a time:

    kubectl set env deployment/closure-api MEMTRACE=1     # then, after the load has run for a while:
    kubectl exec POD -- kill -USR1 1; kubectl exec POD -- cat /tmp/memtrace.txt
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import tracemalloc

log = logging.getLogger("app.memtrace")
_baseline: list = []


def _write(path: str, top: int = 40) -> None:
    snap = tracemalloc.take_snapshot().filter_traces((tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, "<frozen *>")))
    if not _baseline:
        _baseline.append(snap)
        text = "baseline recorded\n"
    else:
        stats = snap.compare_to(_baseline[0], "lineno")
        cur, peak = tracemalloc.get_traced_memory()
        text = f"traced now {cur / 2**20:.1f} MiB (peak {peak / 2**20:.1f}); growth since the baseline, by line:\n"
        text += "\n".join(str(s) for s in stats[:top]) + "\n\nby traceback (top 8, 12 frames):\n"
        for s in snap.compare_to(_baseline[0], "traceback")[:8]:
            text += f"{s.size_diff / 2**20:+.2f} MiB {s.count_diff:+d} blocks\n" + "\n".join("    " + ln for ln in s.traceback.format()[-12:]) + "\n"
    with open(path, "w") as fh:
        fh.write(text)


def install() -> None:
    if os.environ.get("MEMTRACE") != "1":
        return
    tracemalloc.start(12)
    path = os.environ.get("MEMTRACE_OUT", "/tmp/memtrace.txt")
    signal.signal(signal.SIGUSR1, lambda *_: threading.Thread(target=_write, args=(path,), daemon=True).start())
    log.warning("memtrace on: tracemalloc started; SIGUSR1 writes %s", path)
