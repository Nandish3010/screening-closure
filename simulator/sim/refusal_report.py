"""The secondary scenario "with the informed-refusal pathway" (A33, ADR-042) in results.md, rendered from refusal.json.

The headline keeps `orchestration.refusal_path_share` at its default 0 (off). `make evaluate-refusal` runs the same evaluation
with simulator/params.refusal.yaml layered on top (every orchestrated-arm refuser counselled and closed as declined), then
`python -m simulator.sim.refusal_report <its results.json> docs/results` extracts a small summary into docs/results/refusal.json
and splices this section into results.md. `make evaluate` copies the section from the committed refusal.json (it does not
rerun the refusal evaluation), as it does for the capacity section.

    python -m simulator.sim.refusal_report <refusal results.json> <out dir: docs/results>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

BEGIN = "<!-- refusal-section:begin (written by make evaluate-refusal; make evaluate copies it from refusal.json) -->"
END = "<!-- refusal-section:end -->"
UPPER, LITERATURE = "upper_range", "literature_anchored"
TITLE = {UPPER: "upper range", LITERATURE: "literature anchored"}
STUB = ("## Secondary scenario: with the informed-refusal pathway\n\nNot generated yet: `docs/results/refusal.json` does not exist. "
        "Run `make evaluate-refusal`.")
ROOT = Path(__file__).resolve().parents[2]

ARM_KEYS = ("completion_180d", "closed_share", "declined_closed_share", "lost_to_follow_up_rate", "escalations_per_100_cases")
EFFORT_KEYS = ("labour_reduction", "labour_reduction_per_closed_case", "labour_reduction.coordinator",
               "labour_reduction.owner_clinician", "labour_reduction.specialist")


def _stat(s: dict | None) -> dict | None:
    return None if not s else {k: s.get(k) for k in ("mean", "ci_low", "ci_high")}


def assumptions() -> dict:
    """The inputs that shape the path, read from where the simulator and the pathway take them."""
    from .effort import ACTION
    from .engine import COUNSELLING_DELAY_WD
    from .params import load_params

    p = load_params(ROOT / "simulator/params.yaml", ROOT / "simulator/params.refusal.yaml", warn=False)
    pw = yaml.safe_load((ROOT / "config/pathways/colorectal_fit.yaml").read_text())
    return {
        "counselling_delay_working_days": COUNSELLING_DELAY_WD,
        "signoff_sla_working_days": p.int("orchestration.signoff_sla_working_days"),
        "counselling_minutes": ACTION["O_RECORD_REFUSAL"].minutes,
        "re_offer_months": pw["informed_refusal"]["re_offer_months"],
        "re_offer_months_clinical_review": 3,
        "refusal_path_share": p.float("orchestration.refusal_path_share"),
    }


def summarise(res: dict) -> dict:
    meta = res["meta"]
    out = {
        "meta": {k: meta[k] for k in ("seed", "replications", "cases_per_replication", "follow_up_days", "params_files",
                                      "pathway_version")},
        "params_hash": {UPPER: meta["params_hash"][:16], LITERATURE: res["scenarios"][LITERATURE]["params_hash"][:16]},
        "assumptions": assumptions(),
        "scenarios": {},
    }
    for scen in (LITERATURE, UPPER):
        sc = res["scenarios"][scen] if scen in res["scenarios"] else None
        arms = (sc or res)["arms"]
        eff = res["effort"]["scenarios"][scen]
        out["scenarios"][scen] = {
            "orchestrated": {k: _stat(arms["orchestrated"].get(k)) for k in ARM_KEYS},
            "baseline": {k: _stat(arms["baseline"].get(k)) for k in ARM_KEYS},
            "effort_in_reach": {k: _stat(eff["in_reach"]["reduction"].get(k)) for k in EFFORT_KEYS},
            "effort_all": {k: _stat(eff["all"]["reduction"].get(k)) for k in EFFORT_KEYS},
        }
    return out


def _f(v, kind: str) -> str:
    if v is None:
        return "n/a"
    return f"{100 * v:.1f}%" if kind == "pct" else f"{v:.1f}"


def _cell(s: dict | None, kind: str = "pct") -> str:
    if not s or s.get("mean") is None:
        return "n/a"
    if s.get("ci_low") is None:
        return _f(s["mean"], kind)
    return f"{_f(s['mean'], kind)} ({_f(s['ci_low'], kind)} to {_f(s['ci_high'], kind)})"


def _headline(res: dict | None, scen: str, part: str, key: str) -> dict | None:
    """The same statistic from the headline results.json (refusal path off)."""
    if res is None:
        return None
    if part in ("effort_in_reach", "effort_all"):
        block = res["effort"]["scenarios"][scen]["in_reach" if part == "effort_in_reach" else "all"]["reduction"]
        return block.get(key)
    sc = res["scenarios"].get(scen) or res
    return sc["arms"][part].get(key)


ROWS = (
    ("effort_in_reach", "labour_reduction", "pct", "**Labour reduction among the cases the programme reaches**"),
    ("effort_all", "labour_reduction", "pct", "Labour reduction, programme level (all cases)"),
    ("effort_in_reach", "labour_reduction_per_closed_case", "pct", "Labour reduction per closed case, in reach"),
    ("effort_in_reach", "labour_reduction.coordinator", "pct", "Coordinator minutes, reduction, in reach"),
    ("effort_in_reach", "labour_reduction.owner_clinician", "pct", "Owner-clinician minutes, reduction, in reach (negative = more)"),
    ("orchestrated", "completion_180d", "pct", "Colonoscopy completed within 180 days, orchestrated"),
    ("orchestrated", "closed_share", "pct", "Cases closed with evidence and sign-off (colonoscopy), orchestrated"),
    ("orchestrated", "declined_closed_share", "pct", "Cases closed as declined after counselling and a second physician, orchestrated"),
    ("orchestrated", "lost_to_follow_up_rate", "pct", "Lost to follow-up, orchestrated"),
    ("orchestrated", "escalations_per_100_cases", "num", "Escalations per 100 cases, orchestrated"),
)


def render_section(s: dict, res: dict | None = None) -> str:
    a, m = s["assumptions"], s["meta"]
    lit = s["scenarios"][LITERATURE]
    head_in_reach = _headline(res, LITERATURE, "effort_in_reach", "labour_reduction")
    with_in_reach = lit["effort_in_reach"]["labour_reduction"]
    hm = f"{100 * head_in_reach['mean']:.1f}%" if head_in_reach else "the headline figure"
    wm = f"{100 * with_in_reach['mean']:.1f}%"
    out = [
        "## Secondary scenario: with the informed-refusal pathway (not the headline)",
        "",
        f"Generated by `make evaluate-refusal` (seed {m['seed']}, {m['replications']} replications x {m['cases_per_replication']:,} cases x "
        f"{m['follow_up_days']} days, the same cohorts and random numbers as the rest of this file; parameters "
        f"{' + '.join('`' + f + '`' for f in m['params_files'])}, hashes `{s['params_hash'][UPPER]}` (upper range), "
        f"`{s['params_hash'][LITERATURE]}` (literature anchored); summary in `docs/results/refusal.json`).",
        "",
        f"**The headline remains {hm}** labour reduction in reach (literature anchored) with `orchestration.refusal_path_share` at its "
        "default 0: refusers are flagged lost and the escalation ladder runs on for them. This section switches the informed-refusal "
        f"path (A33, ADR-042) on for every orchestrated-arm refuser (`refusal_path_share` {a['refusal_path_share']:g}); the baseline arm is "
        f"unchanged. In reach the labour reduction becomes **{wm}** against {hm}. **The 40% target is not claimed on this basis**: "
        "the path rests on the assumptions below, none of them measured, and adopting it is the results owner's decision after "
        "clinical review.",
        "",
        "**Assumptions (all assumed, none sourced):**",
        "",
        f"- the owner counsels a refuser {a['counselling_delay_working_days']} working days after the process notices the refusal "
        "(`simulator/sim/engine.py`, `COUNSELLING_DELAY_WD`);",
        f"- a different physician signs the declined closure {a['signoff_sla_working_days']} working days later "
        "(`orchestration.signoff_sla_working_days`, the pathway's `close` SLA);",
        f"- counselling and the counselling record take {a['counselling_minutes']:g} minutes of the owner clinician's time "
        "(`O_RECORD_REFUSAL`; the sign-off is charged as a closure signature);",
        f"- the re-offer date is {a['re_offer_months']} months after the refusal (`informed_refusal.re_offer_months`), an assumption: "
        f"the AI-simulated clinical review proposed {a['re_offer_months_clinical_review']} months; the re-offer is not simulated within "
        "the follow-up, so it does not move these numbers;",
        "- every refuser takes the path (share 1.0), the upper end; in practice some refusers will not be reached for counselling.",
        "",
        "Literature-anchored scenario; headline column from the committed `results.json` (path off), the other from `refusal.json` "
        "(path on). Means with percentile-bootstrap 95% intervals over replications.",
        "",
        "| Metric | Headline (path off) | With the informed-refusal path |",
        "|---|---|---|",
    ]
    for part, key, kind, label in ROWS:
        out.append(f"| {label} | {_cell(_headline(res, LITERATURE, part, key), kind)} | {_cell(lit[part].get(key), kind)} |")
    up = s["scenarios"][UPPER]["effort_in_reach"]["labour_reduction"]
    up_head = _headline(res, UPPER, "effort_in_reach", "labour_reduction")
    coord_off = _headline(res, LITERATURE, "effort_in_reach", "labour_reduction.coordinator")
    coord_on = lit["effort_in_reach"]["labour_reduction.coordinator"]
    esc_off = _headline(res, LITERATURE, "orchestrated", "escalations_per_100_cases")
    esc_on = lit["orchestrated"]["escalations_per_100_cases"]
    out += [
        "",
        f"Upper range (mechanism ceiling), labour reduction in reach: {_cell(up_head)} with the path off, {_cell(up)} with it on.",
        "",
        "**Reading.** The extra saving is coordinator time: the coordinator's reduction in reach goes from "
        f"{_cell(coord_off)} to {_cell(coord_on)}, because a refuser who has been counselled and closed as declined is no longer "
        "chased as a lost case. Escalations per 100 cases barely move "
        f"({_f(esc_off['mean'] if esc_off else None, 'num')} against {_f(esc_on['mean'] if esc_on else None, 'num')}), so the "
        "saving is not in the ladder. The owner clinician pays for it (counselling, and a second physician's signature), so "
        "owner-clinician minutes rise further than in the headline. Colonoscopy completion and the lost-to-follow-up share (no "
        "colonoscopy and no progress) do not change: a refuser has no colonoscopy either way; the declined closures are reported "
        "apart from the colonoscopy closures. Timing figures inherit the simulator's unvalidated timing "
        "([`validation.md`](validation.md)).",
    ]
    return "\n".join(out)


def section_block(s: dict | None, res: dict | None = None) -> str:
    return BEGIN + "\n" + (render_section(s, res) if s else STUB) + "\n" + END


def load_refusal(out_dir: Path) -> dict | None:
    p = Path(out_dir) / "refusal.json"
    return json.loads(p.read_text()) if p.exists() else None


def splice_into_results(out_dir: Path) -> bool:
    """Replace the refusal block of results.md in place (or insert it before '## Summary'). False if there is no results.md."""
    md = Path(out_dir) / "results.md"
    if not md.exists():
        return False
    res_path = Path(out_dir) / "results.json"
    res = json.loads(res_path.read_text()) if res_path.exists() else None
    text = md.read_text()
    block = section_block(load_refusal(Path(out_dir)), res)
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
    p = out_dir / "refusal.json"
    p.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    spliced = splice_into_results(out_dir)
    print(f"wrote {p}" + (" and the refusal section of results.md" if spliced else " (no results.md to update)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
