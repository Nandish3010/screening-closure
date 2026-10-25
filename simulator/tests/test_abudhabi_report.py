"""E20b: the Abu Dhabi-shaped baseline overlay and its sensitivity section of results.md, rendered from abudhabi.json."""

import json
from pathlib import Path

from simulator.sim import abudhabi_report as ar
from simulator.sim.params import load_params

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"sourced", "sourced-adjacent", "calibrated", "assumed"}


def _stat(m, lo=None, hi=None):
    return {"mean": m, "ci_low": m - 0.01 if lo is None else lo, "ci_high": m + 0.01 if hi is None else hi}


def _fake_summary():
    def scen(base, orch):
        blk = {"baseline": {}, "orchestrated": {}, "gain": {}, "ratio": {}}
        for h, (b, o) in {90: (base * 0.4, orch * 0.7), 180: (base * 0.6, orch * 0.85), 360: (base, orch)}.items():
            k = f"completion_{h}d"
            blk["baseline"][k], blk["orchestrated"][k] = _stat(b), _stat(o)
            blk["gain"][k], blk["ratio"][k] = _stat(o - b), o / b
        for arm, sl in (("baseline", 0.7), ("orchestrated", 0.3)):
            blk[arm].update(silent_loss_rate=_stat(sl), silent_loss_rate_in_reach=_stat(0.0 if arm == "orchestrated" else sl),
                            closed_share=_stat(0.4))
        return blk

    return {
        "meta": {"seed": 1, "replications": 2, "cases_per_replication": 10, "follow_up_days": 360,
                 "params_files": ["simulator/params.yaml", "simulator/params.abudhabi.yaml"], "pathway_version": "x"},
        "params_hash": {ar.UPPER: "a" * 16, ar.LITERATURE: "b" * 16},
        "target": {"completion_360d": 0.305, "median_days": 72, "followed_up": 436, "positives": 1429},
        "overlay": ar.overlay_entries(),
        "scenarios": {ar.LITERATURE: scen(0.30, 0.50), ar.UPPER: scen(0.30, 0.70)},
    }


def test_the_overlay_loads_over_the_defaults_and_sets_the_sourced_target():
    p = load_params(ROOT / "simulator/params.yaml", ROOT / "simulator/params.abudhabi.yaml", warn=False)
    assert p.float("colonoscopy_completion_12m") == 0.305
    assert p.float("patients.refusal_prob") > 0.25
    assert p.float("appointment.slow_lane_lead_median_days") == 49  # slow-lane timing left at its default


def test_every_overlay_key_has_a_source_and_a_status():
    entries = ar.overlay_entries()
    assert {e["key"] for e in entries} >= {"colonoscopy_completion_12m", "patients.refusal_prob", "appointment.fast_lane_share.unmanaged"}
    for e in entries:
        assert e["status"] in STATUSES, e["key"]
        assert e["source"] and len(str(e["source"])) > 10, e["key"]
        if e["status"] == "calibrated":
            assert "Calibrated (E20b) so the baseline reproduces the 30.5% Abu Dhabi audit" in e["source"], e["key"]


def test_the_section_renders_from_a_tiny_summary_with_the_required_caveats():
    block = ar.section_block(_fake_summary())
    assert block.startswith(ar.BEGIN) and block.endswith(ar.END)
    assert "## Sensitivity: an Abu Dhabi-shaped baseline (not the headline)" in block
    for needle in ("sensitivity run", "not evidence of effect", "before IFHAS V10", "Emirati nationals", "0.55", "programme level",
                   "| 90 days |", "| 180 days |", "| 360 days |", "conservative", "calibrated"):
        assert needle in block, needle
    assert block.index("Literature anchored") < block.index("Upper range")


def test_without_abudhabi_json_the_section_is_a_stub():
    assert "Not generated yet" in ar.section_block(None)


def test_splice_is_idempotent_and_inserts_before_the_summary(tmp_path):
    (tmp_path / "abudhabi.json").write_text(json.dumps(_fake_summary()))
    (tmp_path / "results.md").write_text("# R\n\n## Other\n\ntext\n\n## Summary\n\nbody\n")
    assert ar.splice_into_results(tmp_path)
    once = (tmp_path / "results.md").read_text()
    assert once.count(ar.BEGIN) == 1 and once.index(ar.BEGIN) < once.index("## Summary")
    assert ar.splice_into_results(tmp_path)
    assert (tmp_path / "results.md").read_text() == once


def test_the_committed_section_matches_abudhabi_json():
    summary = json.loads((ROOT / "docs/results/abudhabi.json").read_text())
    assert ar.section_block(summary) in (ROOT / "docs/results/results.md").read_text(), "results.md is stale: run make evaluate"
    # the baseline lands near the audit: completion, completer median, early share
    b = summary["scenarios"][ar.LITERATURE]["baseline"]
    assert abs(b["completion_360d"]["mean"] - 0.305) < 0.015
    assert 60 <= b["time_to_colonoscopy_median"]["mean"] <= 90
    assert b["completion_90d"]["mean"] >= 0.5 * b["completion_360d"]["mean"]
