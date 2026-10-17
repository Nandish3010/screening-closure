"""Ticket B13: docs/results/metrics.md defines every metric key in results.json, and its worked example is true."""

import json
import re
from pathlib import Path

from simulator.sim.scenario import scenario_metrics

RESULTS = Path(__file__).resolve().parents[2] / "docs" / "results"
PLACEHOLDERS = {"<dim>": "(?:language|channel)", "<group>": "[a-z_]+", "<tag>": "[a-z_]+"}


def _heading_tokens() -> list[str]:
    out = []
    for line in (RESULTS / "metrics.md").read_text().splitlines():
        if line.startswith("#"):
            out += re.findall(r"`([^`]+)`", line)
    return out


def _regex(token: str) -> re.Pattern:
    pat = re.escape(token)
    for ph, rx in PLACEHOLDERS.items():
        pat = pat.replace(re.escape(ph), rx)
    return re.compile(pat)


def _result_keys() -> set[str]:
    res = json.loads((RESULTS / "results.json").read_text())
    keys = set()
    for sc in res["scenarios"].values():
        for arm in sc["arms"].values():
            keys |= set(arm)
    keys |= {k for arm in res["arms"].values() for k in arm}
    return keys


def test_every_metric_key_has_a_definition_heading():
    tokens = _heading_tokens()
    patterns = [_regex(t) for t in tokens]
    missing = sorted(k for k in _result_keys() if not any(p.fullmatch(k) for p in patterns))
    assert not missing, f"metrics.md has no definition heading for: {missing}"


def test_every_definition_heading_names_a_real_metric():
    keys = _result_keys()
    stale = [t for t in _heading_tokens() if not any(_regex(t).fullmatch(k) for k in keys)]
    assert not stale, f"metrics.md defines metrics that results.json does not have: {stale}"


def test_the_capacity_caveat_is_prominent_and_its_number_is_the_result():
    res = json.loads((RESULTS / "results.json").read_text())
    doc = (RESULTS / "metrics.md").read_text()
    head = doc[: doc.index("## 1.")]
    assert "Capacity caveat" in head and "cost nothing" in head and "future work" in head
    quoted = int(re.search(r"about (\d+) escalations per 100 cases", head).group(1))
    actual = res["scenarios"]["upper_range"]["arms"]["orchestrated"]["escalations_per_100_cases"]["mean"]
    assert quoted == round(actual)
    md = (RESULTS / "results.md").read_text()
    cap = md.index("## Capacity caveat")
    assert md.index("## Headline") < cap < md.index("## Summary")
    assert f"about {quoted} escalations per 100 cases" in md[cap:md.index("## Summary")]


def test_worked_example_in_the_doc_matches_the_code():
    doc = (RESULTS / "metrics.md").read_text()
    table = doc[doc.index("Machine-checked values"):]
    rows = re.findall(r"^\| `([^`]+)` \| ([0-9.]+) \|$", table, flags=re.M)
    assert len(rows) >= 10
    got = scenario_metrics(min_group_size=1)
    for key, val in rows:
        assert got[key] == float(val), key
