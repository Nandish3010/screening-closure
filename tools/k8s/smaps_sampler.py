#!/usr/bin/env python3
"""M7-8: a native-level look at the API pods' memory without installing anything in them (no py-spy, memray or jemalloc is in the image).

Every --period seconds, for each API pod, reads /proc/1/smaps_rollup, /proc/1/status and /proc/1/smaps (the uvicorn process is PID 1)
through `kubectl exec ... cat` and records: RSS / anonymous / private-dirty totals, the `[heap]` mapping (glibc's main arena, grown with brk),
and the anonymous read-write mappings (glibc's per-thread arenas are 64 MiB mmaps, plus Python's own arenas and big allocations), summed
and counted by size class. If the growth is in `[heap]` and arenas that never shrink while the Python-level allocator (tracemalloc, M7-7)
shows nothing retained, it is fragmentation / retention in malloc, not a leak of live objects.

    python tools/k8s/smaps_sampler.py --out docs/results/scale/m78/smaps.json --period 900
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

CTX, NS = "kind-sc-m76", "closure"
HDR = re.compile(r"^([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$")


def kubectl(*a: str, timeout: float = 60) -> str:
    return subprocess.run(["kubectl", "--context", CTX, "-n", NS, *a], capture_output=True, text=True, timeout=timeout).stdout


def parse(text: str) -> dict:
    maps, cur = [], None
    for ln in text.splitlines():
        m = HDR.match(ln)
        if m:
            cur = {"size_kb": (int(m[2], 16) - int(m[1], 16)) // 1024, "perm": m[3], "name": m[4].strip()}
            maps.append(cur)
        elif cur and ":" in ln:
            k, v = ln.split(":", 1)
            if k in ("Rss", "Anonymous", "Private_Dirty"):
                cur[k.lower()] = int(v.split()[0])
    heap = [x for x in maps if x["name"] == "[heap]"]
    anon = [x for x in maps if not x["name"] and x["perm"].startswith("rw")]
    big = [x for x in anon if 60000 <= x["size_kb"] <= 70000]  # glibc thread arenas are 64 MiB reservations
    return {
        "heap_rss_kb": sum(x.get("rss", 0) for x in heap), "heap_size_kb": sum(x["size_kb"] for x in heap),
        "anon_rw_mappings": len(anon), "anon_rw_rss_kb": sum(x.get("rss", 0) for x in anon),
        "arena_like_64mib_mappings": len(big), "arena_like_rss_kb": sum(x.get("rss", 0) for x in big),
        "other_anon_rss_kb": sum(x.get("rss", 0) for x in anon if x not in big),
        "file_backed_rss_kb": sum(x.get("rss", 0) for x in maps if x["name"].startswith("/")),
        "largest_anon_rss_kb": sorted((x.get("rss", 0) for x in anon), reverse=True)[:5],
    }


def sample(pod: str) -> dict:
    status = kubectl("exec", pod, "--", "sh", "-c", "cat /proc/1/status; echo ====; cat /proc/1/smaps_rollup; echo ====; cat /proc/1/smaps")
    parts = status.split("====\n")
    st = {k: int(v.split()[0]) for ln in parts[0].splitlines() if ":" in ln for k, v in [ln.split(":", 1)]
          if k in ("VmRSS", "RssAnon", "RssFile", "VmData", "Threads") and v.split()}
    roll = {k: int(v.split()[0]) for ln in parts[1].splitlines() if ":" in ln for k, v in [ln.split(":", 1)] if v.split()[:1] and v.split()[0].isdigit()} if len(parts) > 1 else {}
    return {"status_kb": st, "smaps_rollup_kb": {k: roll[k] for k in ("Rss", "Pss", "Anonymous", "Private_Dirty") if k in roll}, **(parse(parts[2]) if len(parts) > 2 else {})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--period", type=float, default=900)
    ap.add_argument("--until", type=float, default=None, help="stop after this many seconds (default: run until killed)")
    a = ap.parse_args()
    out = Path(a.out)
    doc = {"tool": "tools/k8s/smaps_sampler.py", "started_at": datetime.now(UTC).isoformat(), "samples": []}
    t0 = time.time()
    while a.until is None or time.time() - t0 < a.until:
        pods = kubectl("get", "pods", "-l", "app.kubernetes.io/name=api", "-o", "jsonpath={.items[*].metadata.name}").split()
        row = {"t": datetime.now(UTC).isoformat(), "t_epoch": round(time.time(), 1), "pods": {}}
        for p in pods:
            try:
                row["pods"][p] = sample(p)
            except Exception as exc:  # noqa: BLE001
                row["pods"][p] = {"error": str(exc)[:200]}
        doc["samples"].append(row)
        out.write_text(json.dumps(doc, indent=1) + "\n")
        time.sleep(max(0.0, a.period - (time.time() - row["t_epoch"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
