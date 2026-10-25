#!/usr/bin/env python3
"""Generate web/src/judge/headline.json (the /judge page figures) from docs/results/results.json.

Deterministic: sorted keys, indent 2, trailing newline, no timestamps. Stdlib only.
    python tools/judge/make_headline.py            write the file
    python tools/judge/make_headline.py --check    exit 1 if the committed file differs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "results" / "results.json"
OUT = ROOT / "web" / "src" / "judge" / "headline.json"
SCENARIO = "literature_anchored"


def pct(x: float) -> dict:
    return {"value": f"{x * 100:.1f}%", "mean": x}


def build(results: dict) -> dict:
    s = results["scenarios"][SCENARIO]
    arms = s["arms"]
    eff = results["effort"]["scenarios"]
    base, orch = arms["baseline"], arms["orchestrated"]
    c_b, c_o = base["completion_180d"]["mean"], orch["completion_180d"]["mean"]
    ratio = c_o / c_b
    lit_all = eff[SCENARIO]["all"]["reduction"]["labour_reduction"]
    lit_reach = eff[SCENARIO]["in_reach"]["reduction"]["labour_reduction"]
    up_all = eff["upper_range"]["all"]["reduction"]["labour_reduction"]
    meta = results["meta"]
    return {
        "scenario": {"key": SCENARIO, "label": s["label"], "params_hash": s["params_hash"]},
        "run": {
            "cases_per_replication": meta["cases_per_replication"],
            "follow_up_days": meta["follow_up_days"],
            "git_commit": meta["git_commit"],
            "replications": meta["replications"],
        },
        "completion_180d": {"baseline": pct(c_b), "orchestrated": pct(c_o),
                            "ratio": {"value": f"{ratio:.2f}", "mean": ratio}},
        "closed_share": {"baseline": pct(base["closed_share"]["mean"]),
                         "orchestrated": pct(orch["closed_share"]["mean"])},
        # The baseline column of the headline table's in-reach row (results.md) is the baseline silent_loss_rate (45.3%).
        "silent_loss": {
            "baseline": pct(base["silent_loss_rate"]["mean"]),
            "orchestrated_in_reach": pct(orch["silent_loss_rate_in_reach"]["mean"]),
            "orchestrated_programme": pct(orch["silent_loss_rate"]["mean"]),
        },
        "labour_reduction": {
            "literature_all": pct(lit_all["mean"]),
            "literature_in_reach": pct(lit_reach["mean"]),
            "upper_all": pct(up_all["mean"]),
            "upper_all_ci_low": pct(up_all["ci_low"]),
            "upper_all_ci_high": pct(up_all["ci_high"]),
        },
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def generate() -> str:
    return render(build(json.loads(SRC.read_text())))


def main(argv: list[str]) -> int:
    text = generate()
    if "--check" in argv:
        have = OUT.read_text() if OUT.exists() else ""
        if have != text:
            print(f"{OUT.relative_to(ROOT)} is out of date with {SRC.relative_to(ROOT)}; run `make judge-headline` and commit it.", file=sys.stderr)
            return 1
        print("judge headline: up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
