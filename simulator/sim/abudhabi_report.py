"""The sensitivity section "an Abu Dhabi-shaped baseline" (E20b) in results.md, rendered from abudhabi.json.

The headline baseline is calibrated to the US usual-care curve (Mohl 2023: 56.1% colonoscopy at 360 days). Abu Dhabi's own audit
(Almansoori et al., BMC Cancer 2021: 436 of 1,429 positives, 30.5%, "ever") is lower. `make evaluate-abudhabi` runs the same evaluation
with simulator/params.abudhabi.yaml layered on top (baseline process calibrated to 30.5% at 360 days, the unattended-referral share
applied to both arms), then `python -m simulator.sim.abudhabi_report <its results.json> docs/results` extracts a small summary into
docs/results/abudhabi.json and splices this section into results.md. `make evaluate` copies the section from the committed
abudhabi.json (it does not rerun the overlay evaluation), as it does for the refusal and capacity sections.

    python -m simulator.sim.abudhabi_report <abudhabi results.json> <out dir: docs/results>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

BEGIN = "<!-- abudhabi-section:begin (written by make evaluate-abudhabi; make evaluate copies it from abudhabi.json) -->"
END = "<!-- abudhabi-section:end -->"
UPPER, LITERATURE = "upper_range", "literature_anchored"
TITLE = {UPPER: "upper range", LITERATURE: "literature anchored"}
STUB = ("## Sensitivity: an Abu Dhabi-shaped baseline (not the headline)\n\nNot generated yet: `docs/results/abudhabi.json` does not exist. "
        "Run `make evaluate-abudhabi`.")
ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "simulator/params.abudhabi.yaml"
HORIZONS = (90, 180, 360)
STATUS_ORDER = ("sourced", "sourced-adjacent", "calibrated", "assumed")


def _stat(s: dict | None) -> dict | None:
    return None if not s else {k: s.get(k) for k in ("mean", "ci_low", "ci_high")}


def overlay_entries(path: Path = OVERLAY) -> list[dict]:
    """Every leaf of the overlay file that carries a `value`, with its dotted key, value, status, source and notes."""
    out: list[dict] = []

    def walk(node: dict, prefix: str) -> None:
        for k, v in node.items():
            if isinstance(v, dict) and "value" in v:
                out.append({"key": prefix + k, "value": v["value"], "status": v.get("status"), "source": v.get("source"),
                            "notes": v.get("notes")})
            elif isinstance(v, dict):
                walk(v, prefix + k + ".")

    walk(yaml.safe_load(Path(path).read_text()) or {}, "")
    return out


def summarise(res: dict) -> dict:
    meta = res["meta"]
    out = {
        "meta": {k: meta[k] for k in ("seed", "replications", "cases_per_replication", "follow_up_days", "params_files",
                                      "pathway_version")},
        "params_hash": {UPPER: meta["params_hash"][:16], LITERATURE: res["scenarios"][LITERATURE]["params_hash"][:16]},
        "target": {"completion_360d": 0.305, "median_days": 72, "followed_up": 436, "positives": 1429},
        "overlay": overlay_entries(),
        "scenarios": {},
    }
    for scen in (LITERATURE, UPPER):
        sc = res["scenarios"][scen] if scen in res["scenarios"] else res
        arms, diff = sc["arms"], sc["difference"]
        block: dict = {"gain": {}, "ratio": {}, "baseline": {}, "orchestrated": {}}
        for h in HORIZONS:
            key = f"completion_{h}d"
            b, o = _stat(arms["baseline"].get(key)), _stat(arms["orchestrated"].get(key))
            block["baseline"][key], block["orchestrated"][key] = b, o
            block["gain"][key] = _stat(diff.get(key))
            block["ratio"][key] = (o["mean"] / b["mean"]) if b and o and b["mean"] else None
        for arm in ("baseline", "orchestrated"):
            for k in ("silent_loss_rate", "silent_loss_rate_in_reach", "closed_share", "time_to_colonoscopy_median"):
                block[arm][k] = _stat(arms[arm].get(k))
        out["scenarios"][scen] = block
    return out


def _pct(v, d: int = 1) -> str:
    return "n/a" if v is None else f"{100 * v:.{d}f}%"


def _cell(s: dict | None) -> str:
    if not s or s.get("mean") is None:
        return "n/a"
    if s.get("ci_low") is None:
        return _pct(s["mean"])
    return f"{_pct(s['mean'])} ({_pct(s['ci_low'])} to {_pct(s['ci_high'])})"


def _days(s: dict | None) -> str:
    if not s or s.get("mean") is None:
        return "n/a"
    return f"{s['mean']:.0f} d ({s['ci_low']:.0f} to {s['ci_high']:.0f})"


def _pts(s: dict | None) -> str:
    if not s or s.get("mean") is None:
        return "n/a"
    return f"{100 * s['mean']:+.1f} ({100 * s['ci_low']:+.1f} to {100 * s['ci_high']:+.1f})"


def render_section(s: dict) -> str:
    m, lit, up = s["meta"], s["scenarios"][LITERATURE], s["scenarios"][UPPER]
    b360, o360 = lit["baseline"]["completion_360d"]["mean"], lit["orchestrated"]["completion_360d"]["mean"]
    sl_b, sl_o = lit["baseline"]["silent_loss_rate"], lit["orchestrated"]["silent_loss_rate"]
    out = [
        "## Sensitivity: an Abu Dhabi-shaped baseline (not the headline)",
        "",
        f"Generated by `make evaluate-abudhabi` (seed {m['seed']}, {m['replications']} replications x {m['cases_per_replication']:,} cases x "
        f"{m['follow_up_days']} days, the same cohorts and random numbers as the rest of this file; parameters "
        f"{' + '.join('`' + f + '`' for f in m['params_files'])}, hashes `{s['params_hash'][UPPER]}` (upper range), "
        f"`{s['params_hash'][LITERATURE]}` (literature anchored); summary in `docs/results/abudhabi.json`).",
        "",
        f"**This is a sensitivity run, not the headline.** The headline baseline is the US usual-care curve (Mohl 2023). Here the baseline "
        f"is calibrated to the Abu Dhabi follow-up audit instead: {s['target']['followed_up']} of {s['target']['positives']:,} positives "
        f"({_pct(s['target']['completion_360d'])}) ever had a colonoscopy (Almansoori et al., BMC Cancer 2021, "
        "[`docs/evidence.md`](../evidence.md)). That audit covers Emirati nationals in 2015-2016, before IFHAS V10, and gives one 'ever' "
        f"figure, with a median of {s['target']['median_days']} days among those who had one and more than half of them within 90 days; the baseline is fitted to the 360-day share, to that median and to that 90-day split. **Programme reach "
        "(`orchestration.programme_reach`, 0.55, calibrated against the US baseline and the pooled navigation risk ratio) and every "
        "orchestration parameter are unchanged. The ratio in this section is therefore not evidence of effect**: it follows from "
        "the lower baseline and the unchanged reach, and no study measured it in Abu Dhabi.",
        "",
        f"**If Abu Dhabi today looks like the 30.5% audit, the same mechanism moves it to {_pct(o360)} at 360 days "
        f"(literature anchored, simulated, sensitivity run), from {_pct(b360)} simulated for the baseline.** Today's baseline may be better or worse "
        "than 2015-2016. **The overlay is conservative about the mechanism**: the loss the audit does not explain is placed in "
        "`patients.refusal_prob`, a patient-level trait in both arms that the orchestration cannot recover, so the gain here probably "
        "understates what ownership and timers could do on the part of that loss that is really missed hand-offs.",
        "",
    ]
    for scen in (LITERATURE, UPPER):
        sc = s["scenarios"][scen]
        out += [f"**{TITLE[scen].capitalize()}.** Means with percentile-bootstrap 95% intervals over replications; gain is orchestrated minus baseline, in percentage points.",
                "",
                "| Colonoscopy completed within | Baseline | Orchestrated | Gain (points) | Ratio |", "|---|---|---|---|---|"]
        for h in HORIZONS:
            k = f"completion_{h}d"
            r = sc["ratio"][k]
            out.append(f"| {h} days | {_cell(sc['baseline'][k])} | {_cell(sc['orchestrated'][k])} | {_pts(sc['gain'][k])} | "
                       f"{'n/a' if r is None else f'{r:.2f}'} |")
        out += [
            "",
            "| Other | Baseline | Orchestrated |", "|---|---|---|",
            f"| Silent loss, programme level (every case) | {_cell(sc['baseline']['silent_loss_rate'])} | {_cell(sc['orchestrated']['silent_loss_rate'])} |",
            f"| Silent loss, among the cases the programme reaches | {_cell(sc['baseline']['silent_loss_rate_in_reach'])} | {_cell(sc['orchestrated']['silent_loss_rate_in_reach'])} |",
            f"| Median days from positive result to colonoscopy, among completers within {m['follow_up_days']} days | {_days(sc['baseline'].get('time_to_colonoscopy_median'))} | {_days(sc['orchestrated'].get('time_to_colonoscopy_median'))} |",
            f"| Cases closed with evidence and sign-off | {_cell(sc['baseline']['closed_share'])} | {_cell(sc['orchestrated']['closed_share'])} |",
            "",
        ]
    out += [
        f"Silent loss is {_pct(sl_b['mean'])} -> {_pct(sl_o['mean'])} at programme level (literature anchored); the 0% among the cases the "
        "programme reaches is true by construction, because every reached case has an owner and a timer. The upper-range scenario is the mechanism "
        "ceiling and is not a forecast.",
        "",
        "**What the overlay changes** (everything else is inherited from `simulator/params.yaml`; the literature-anchored scenario then adds "
        "`simulator/params.conservative.yaml` as usual). Status vocabulary as in `simulator/params.yaml`: sourced, sourced-adjacent, calibrated (fitted "
        "here to the audit) or assumed.",
        "",
        "| Key | Overlay value | Status | Basis |", "|---|---|---|---|",
    ]
    for e in sorted(s["overlay"], key=lambda e: (STATUS_ORDER.index(e["status"]) if e["status"] in STATUS_ORDER else 9, e["key"])):
        src = " ".join(str(e["source"]).split())
        out.append(f"| `{e['key']}` | {e['value']} | {e['status']} | {src} |")
    out += [
        "",
        "`gi_referral_non_attendance_abu_dhabi` (0.25) is not read by the engine. It is carried by `patients.refusal_prob`, which applies "
        "in both arms, so the orchestration is assumed to recover none of those patients. The audit's 25% does not explain the whole 69.5% "
        "not followed up, so `patients.refusal_prob` is calibrated higher and the rest is treated the same way. A smaller change to the "
        "unmanaged fast-lane share fits the completer median; slow-lane timing is at its default. 3 and 6 month targets keep their US values.",
    ]
    return "\n".join(out)


def section_block(s: dict | None) -> str:
    return BEGIN + "\n" + (render_section(s) if s else STUB) + "\n" + END


def load_abudhabi(out_dir: Path) -> dict | None:
    p = Path(out_dir) / "abudhabi.json"
    return json.loads(p.read_text()) if p.exists() else None


def splice_into_results(out_dir: Path) -> bool:
    """Replace the Abu Dhabi block of results.md in place (or insert it before '## Summary'). False if there is no results.md."""
    md = Path(out_dir) / "results.md"
    if not md.exists():
        return False
    text = md.read_text()
    block = section_block(load_abudhabi(Path(out_dir)))
    if BEGIN in text and END in text:
        i, j = text.index(BEGIN), text.index(END) + len(END)
        text = text[:i] + block + text[j:]
    else:
        k = text.index("## Summary")
        text = text[:k] + block + "\n\n" + text[k:]
    md.write_text(text)
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    src, out_dir = Path(argv[0]), Path(argv[1])
    summary = summarise(json.loads(src.read_text()))
    p = out_dir / "abudhabi.json"
    p.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    spliced = splice_into_results(out_dir)
    print(f"wrote {p}" + (" and the Abu Dhabi section of results.md" if spliced else " (no results.md to update)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
