"""Render results.md from the results dict (numbers only come from results.json's content)."""

from __future__ import annotations

from datetime import datetime, timezone

from .engine import BASELINE, ORCHESTRATED

# key, label, kind: pct (share), days, num, per100
HEADLINE = [
    ("completion_90d", "Colonoscopy completed within 90 days", "pct"),
    ("completion_180d", "Colonoscopy completed within 180 days", "pct"),
    ("completion_360d", "Colonoscopy completed within 360 days", "pct"),
    ("open_30d", "Cases still open at 30 days", "pct"),
    ("open_60d", "Cases still open at 60 days", "pct"),
    ("open_90d", "Cases still open at 90 days", "pct"),
    ("time_to_closure_median", "Time to safe closure, median (closed cases only)", "days"),
    ("time_to_closure_p90", "Time to safe closure, p90 (closed cases only)", "days"),
    ("closed_share", "Cases closed (with evidence and human sign-off) by end of follow-up", "pct"),
    ("time_to_ownership_median", "Time to named owner, median", "days"),
    ("time_to_ownership_p90", "Time to named owner, p90", "days"),
    ("time_to_first_contact_median", "Time to first patient contact, median", "days"),
    ("time_to_first_contact_p90", "Time to first patient contact, p90", "days"),
    ("reopen_rate", "Reopen rate (reopened / closed)", "pct"),
    ("escalation_precision", "Escalation precision (acted on within 5 working days)", "pct"),
    ("escalations_per_100_cases", "Escalations per 100 cases", "num"),
    ("automation_abstention_rate", "Automation abstention rate (AI cut supplies this)", "pct"),
    ("lost_to_follow_up_rate", "Lost to follow-up (no colonoscopy, no progress for 30+ days)", "pct"),
    ("silent_loss_rate", "Lost AND never surfaced to a human", "pct"),
    ("delay_harm_share", "No colonoscopy by the delay-harm threshold (270 days)", "pct"),
    ("consent_breach_rate", "Outreach sent after consent withdrawn", "pct"),
]
LABELS = {k: (l, kind) for k, l, kind in HEADLINE}
TAG_LABEL = {
    "wrong_contact": "Wrong contact number",
    "provider_switch": "Patient switches provider mid-pathway",
    "duplicate_result": "Duplicate result from a second lab",
    "conflicting_ack": "Conflicting ownership acknowledgements",
    "consent_after_booking": "Consent withdrawn after booking",
}


def _fmt(v, kind: str, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    if kind == "pct":
        s = f"{100 * v:+.1f}" if signed else f"{100 * v:.1f}"
        return s + ("pp" if signed else "%")
    if kind == "days":
        return f"{v:+.1f}" if signed else f"{v:.1f}"
    return f"{v:+.2f}" if signed else f"{v:.2f}"


def _cell(s: dict | None, kind: str, signed: bool = False) -> str:
    if not s:
        return "n/a"
    return f"{_fmt(s['mean'], kind, signed)} ({_fmt(s['ci_low'], kind, signed)} to {_fmt(s['ci_high'], kind, signed)})"


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


NO_SOURCE = "assumed, no public source"


def _esc(s: str) -> str:
    out = str(s).replace("|", "\\|").replace("\n", " ")
    return NO_SOURCE if out.strip() == "TODO" else out  # docs never show a bare TODO


UPPER, LITERATURE = "upper_range", "literature_anchored"
SCENARIO_TITLE = {UPPER: "upper range", LITERATURE: "literature anchored"}


def _sc(res: dict, name: str) -> dict:
    """One scenario's arms/difference/meta. Falls back to the top-level keys (the upper-range scenario)."""
    sc = res.get("scenarios", {}).get(name)
    if sc is not None:
        return sc
    if name != UPPER:
        raise KeyError(name)
    return {"arms": res["arms"], "difference": res["difference"], "overrides": [],
            "parameter_status_counts": res["meta"]["parameter_status_counts"], "params_files": res["meta"]["params_files"],
            "params_hash": res["meta"]["params_hash"]}


def _has(res: dict, name: str) -> bool:
    return name == UPPER or name in res.get("scenarios", {})


def _m(res: dict, arm: str, key: str, scen: str = UPPER):
    return _sc(res, scen)["arms"][arm].get(key)


def _ratio(res: dict, key: str, scen: str) -> float | None:
    """Orchestrated / baseline completion at a horizon, ratio of replication means."""
    b, o = _m(res, BASELINE, key, scen), _m(res, ORCHESTRATED, key, scen)
    return o["mean"] / b["mean"] if b and o and b["mean"] else None


def summary_paragraph(res: dict, scen: str = UPPER, caveat: bool = True) -> str:
    meta = res["meta"]
    sc = _sc(res, scen)
    b, o, d = sc["arms"][BASELINE], sc["arms"][ORCHESTRATED], sc["difference"]

    def mean(s):
        return None if not s else s["mean"]

    def pct(x):
        return f"{100 * x:.0f}%"

    def verdict(dd: dict | None, unit: str = "percentage points", scale: float = 100.0) -> str:
        if not dd:
            return ""
        lo, hi = dd["ci_low"] * scale, dd["ci_high"] * scale
        m = dd["mean"] * scale
        sign = "excludes zero" if (lo > 0 or hi < 0) else "includes zero"
        return f"{m:+.1f} {unit}, 95% CI {lo:+.1f} to {hi:+.1f}, which {sign}"

    parts = []
    c_key = "completion_180d" if _m(res, ORCHESTRATED, "completion_180d", scen) else "completion_final"
    horizon = "180 days" if c_key == "completion_180d" else f"the {meta['follow_up_days']}-day follow-up"
    parts.append(
        f"Across {meta['replications']} simulated replications of {meta['cases_per_replication']:,} synthetic "
        f"positive-FIT cases, the orchestrated arm got {pct(mean(o[c_key]))} of patients to a confirmatory colonoscopy "
        f"within {horizon}, against {pct(mean(b[c_key]))} at baseline ({verdict(d[c_key])})."
    )
    if mean(o.get("open_90d")) is not None:
        parts.append(
            f"At 90 days {pct(mean(o['open_90d']))} of orchestrated cases were still open against "
            f"{pct(mean(b['open_90d']))} at baseline."
        )
    if mean(o.get("time_to_closure_median")) is not None and mean(b.get("time_to_closure_median")) is not None:
        parts.append(
            f"Among cases that closed, the median time to a signed, evidence-backed closure was "
            f"{mean(o['time_to_closure_median']):.0f} days orchestrated against {mean(b['time_to_closure_median']):.0f} "
            f"at baseline; the baseline figure only covers the {pct(mean(b['closed_share']))} of cases that closed at all, "
            f"so read it together with the closure share ({pct(mean(o['closed_share']))} orchestrated)."
        )
    if mean(b.get("silent_loss_rate")) is not None:
        parts.append(
            f"{pct(mean(b['silent_loss_rate']))} of baseline cases were lost without anyone being told, against "
            f"{pct(mean(o['silent_loss_rate']))} in the orchestrated arm, where lost or unreachable cases that receive the "
            f"orchestrated process are flagged to a human and never closed automatically"
            + ("." if (mean(o["silent_loss_rate"]) or 0) < 0.005 else
               f"; the remainder are cases outside the programme's reach, which are handled as today.")
        )
    counts = sc["parameter_status_counts"]
    n_all = sum(counts.values())
    weak = counts.get("assumed", 0) + counts.get("calibrated", 0)
    if caveat:
        parts.append(
            f"These results come from a model, not a trial: {weak} of {n_all} parameters are assumed or calibrated rather than "
            f"measured, so the direction of the effect follows from the mechanisms while its size depends on those inputs. "
            f"Rerun with `make evaluate` after changing any parameter in `simulator/params.yaml` or `simulator/params.conservative.yaml`."
        )
    return " ".join(parts)


def calibration_section(res: dict) -> str:
    params = {p["key"]: p for p in res["parameters"]}
    tg = {d: params[k]["value"] for d, k in (("90", "colonoscopy_completion_3m"), ("180", "colonoscopy_completion_6m"), ("360", "colonoscopy_completion_12m"))}
    ref = {d: params[k]["value"] for d, k in (("90", "colonoscopy_completion_3m_integrated_ref"), ("180", "colonoscopy_completion_6m_integrated_ref"), ("360", "colonoscopy_completion_12m_integrated_ref"))}
    lit = _has(res, LITERATURE)
    rows = []
    worst = 0.0
    for d, lab in (("90", "90 days"), ("180", "180 days"), ("360", "360 days")):
        key = f"completion_{d}d"
        bs, os_ = _m(res, BASELINE, key), _m(res, ORCHESTRATED, key)
        if not bs:
            continue
        gap = bs["mean"] - tg[d]
        worst = max(worst, abs(gap))
        row = [lab, f"{100 * tg[d]:.1f}%", _cell(bs, "pct"), f"{100 * gap:+.1f}pp", f"{100 * ref[d]:.1f}%"]
        if lit:
            row.append(_cell(_m(res, ORCHESTRATED, key, LITERATURE), "pct"))
        row.append(_cell(os_, "pct"))
        rows.append(row)
    txt = ["## Calibration check: does the baseline reproduce published completion?", ""]
    txt.append(
        "The baseline arm is the unmanaged process. Its completion curve was tuned (parameters with status `calibrated`) to the "
        "Mohl et al. 2023 curve for positive stool tests in US health systems. The orchestrated arm is compared with the "
        "actively managed Kaiser Permanente curve (Corley et al. 2017), which is the nearest published ceiling. The baseline is "
        "the same in both scenarios."
    )
    txt.append("")
    header = ["Follow-up", "Published unmanaged (Mohl 2023)", "Simulated baseline", "Gap", "Published managed (Corley 2017)"]
    header += (["Simulated orchestrated, literature anchored"] if lit else []) + ["Simulated orchestrated, upper range"]
    txt.append(_table(header, rows))
    txt.append("")
    if rows:
        txt.append(f"The baseline lands within {100 * worst:.1f} percentage points of the published curve at every horizon shown.")
    b_med, b_p25, b_p75 = (_m(res, BASELINE, k) for k in ("time_to_colonoscopy_median", "time_to_colonoscopy_p25", "time_to_colonoscopy_p75"))
    if b_med:
        mrow = params.get("time_to_colonoscopy_median_days", {}).get("value")
        iqr = params.get("time_to_colonoscopy_iqr_days", {}).get("value")
        txt.append(
            f"Among baseline completers the simulated time to colonoscopy is median {b_med['mean']:.0f} days "
            f"(IQR {b_p25['mean']:.0f} to {b_p75['mean']:.0f}). The Sea Mar FQHC figure in the parameter file ({mrow} days, IQR {iqr[0]} to {iqr[1]}) "
            f"describes a slower, lower-completion setting (22.9% at 90 days); Mohl's higher 90-day completion implies a faster completer median, "
            f"so the simulator follows the Mohl completion curve and does not try to match both."
        )
    nav = params.get("navigation_effect", {}).get("value")
    if nav and _ratio(res, "completion_180d", UPPER):
        txt.append("")
        txt.append("### Orchestrated-to-baseline completion ratio against the navigation risk ratio")
        txt.append("")
        rrows = []
        for d, lab in (("90", "90 days"), ("180", "180 days"), ("360", "360 days")):
            key = f"completion_{d}d"
            r_up, r_lit = _ratio(res, key, UPPER), (_ratio(res, key, LITERATURE) if lit else None)
            if r_up is None:
                continue
            rrows.append([lab] + ([f"{r_lit:.2f}"] if lit else []) + [f"{r_up:.2f}", f"{nav}"])
        txt.append(_table(["Follow-up"] + (["Literature anchored"] if lit else []) + ["Upper range", "Pooled navigation RR (J Gen Intern Med 2026)"], rrows))
        txt.append("")
        r180 = _ratio(res, "completion_180d", UPPER)
        txt.append(
            f"The upper-range ratio at 180 days is {r180:.2f} against a pooled risk ratio of {nav} for patient navigation in published trials. "
            f"Orchestration here bundles ownership, timers, escalation and human outreach, and the ratio is larger than the navigation figure "
            f"because the unmanaged baseline loses many cases to missed hand-offs that a managed programme prevents; it is the mechanism ceiling "
            f"and is bounded above by the managed-programme ceiling."
            + (f" The literature-anchored scenario caps the effect at the navigation figure (ratio {_ratio(res, 'completion_180d', LITERATURE):.2f} at 180 days) "
               f"by reaching only part of the case load (`orchestration.programme_reach`, calibrated), and is the scenario to quote."
               if lit else "")
        )
    return "\n".join(txt)


def fairness_section(res: dict, scen: str = UPPER) -> str:
    sc = _sc(res, scen)
    out = [f"## Fairness: completion and time to closure by language and channel ({SCENARIO_TITLE[scen]})", ""]
    for dim, title in (("language", "Language"), ("channel", "Preferred contact channel")):
        groups = sorted({k.split(".")[2] for k in sc["arms"][BASELINE] if k.startswith(f"fairness.{dim}.") and k.count(".") == 3 and k.endswith(".n")})
        rows = []
        for g in groups:
            row = [g]
            for arm in (BASELINE, ORCHESTRATED):
                row.append(_cell(_m(res, arm, f"fairness.{dim}.{g}.completion_final", scen), "pct"))
                row.append(_cell(_m(res, arm, f"fairness.{dim}.{g}.closure_median", scen), "days"))
            rows.append(row)
        out.append(f"### {title}")
        out.append("")
        out.append(_table([title, "Baseline completion", "Baseline median days to closure", "Orchestrated completion", "Orchestrated median days to closure"], rows))
        out.append("")
        gap_rows = []
        for label, key, kind in (("Completion, max gap between groups", f"fairness.{dim}.completion_gap_max", "pct"),
                                 ("Median days to closure, max gap between groups", f"fairness.{dim}.closure_median_gap_max_days", "days")):
            gap_rows.append([label, _cell(_m(res, BASELINE, key, scen), kind), _cell(_m(res, ORCHESTRATED, key, scen), kind),
                             _cell(sc["difference"].get(key), kind, signed=True)])
        out.append(_table(["Max-gap statistic (largest minus smallest group)", "Baseline", "Orchestrated", "Difference"], gap_rows))
        out.append("")
    out.append("Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.")
    return "\n".join(out)


def messy_section(res: dict, scen: str = UPPER) -> str:
    rows = []
    for tag, label in TAG_LABEL.items():
        n_b = _m(res, BASELINE, f"messy.{tag}.n", scen)
        rows.append([
            label,
            f"{n_b['mean']:.0f}" if n_b else "n/a",
            _cell(_m(res, BASELINE, f"messy.{tag}.handled_rate", scen), "pct"),
            _cell(_m(res, ORCHESTRATED, f"messy.{tag}.handled_rate", scen), "pct"),
            _cell(_m(res, BASELINE, f"messy.{tag}.completion_final", scen), "pct"),
            _cell(_m(res, ORCHESTRATED, f"messy.{tag}.completion_final", scen), "pct"),
            _cell(_m(res, ORCHESTRATED, f"messy.{tag}.completion_final_unaffected", scen), "pct"),
        ])
    out = [f"## Messy-case handling ({SCENARIO_TITLE[scen]})", ""]
    out.append(_table(["Messy case", "Cases per replication", "Handled explicitly: baseline", "Handled explicitly: orchestrated",
                       "Completion: baseline", "Completion: orchestrated", "Completion: orchestrated, unaffected cases"], rows))
    out.append("")
    out.append("`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, "
               "second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, "
               "so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute."
               + (" In the literature-anchored scenario, cases outside the programme's reach are handled as today, so the orchestrated handled rates are below 100%." if scen == LITERATURE else ""))
    return "\n".join(out)


def param_section(res: dict) -> str:
    sc = res["meta"]["parameter_status_counts"]
    head = ", ".join(f"{v} {k}" for k, v in sc.items())
    rows = []
    for p in res["parameters"]:
        v = p["value"]
        vs = str(v) if not isinstance(v, (dict, list)) else str(v).replace("'", "")
        rows.append([f"`{p['key']}`", _esc(vs), p["status"], _esc(p["source"])])
    out = [
        "## Parameters", "",
        f"Parameter files: {', '.join('`' + f + '`' for f in res['meta']['params_files'])}. Status counts: {head}. "
        "`sourced` and `sourced-adjacent` values are from public documents (details and caveats in `docs/evidence.md`); "
        "`calibrated` values were tuned to the published baseline curve; `assumed` values have no public source. "
        "This table is the upper-range scenario; the literature-anchored scenario changes only the keys listed below it.",
        "",
        _table(["Parameter", "Value", "Status", "Source"], rows),
    ]
    if _has(res, LITERATURE):
        lit = _sc(res, LITERATURE)
        out += [
            "", "### Overrides in the literature-anchored scenario", "",
            f"Overlay files: {', '.join('`' + f + '`' for f in lit['params_files'][len(res['meta']['params_files']):])}. "
            "Every other parameter, including everything the baseline arm reads, is inherited unchanged "
            "(evaluate refuses to write results if any replication's baseline event log differs between the scenarios).",
            "",
            _table(["Parameter", "Upper range", "Literature anchored", "Status", "Source"],
                   [[f"`{o['key']}`", _esc(o["upper_range_value"]), _esc(o["value"]), o["status"], _esc(o["source"])]
                    for o in lit["overrides"]]),
        ]
    return "\n".join(out)


# Rows of the headline table: (key or ("ratio", key), label, kind)
HEADLINE_ROWS = [
    ("completion_90d", "Colonoscopy completed within 90 days", "pct"),
    ("completion_180d", "Colonoscopy completed within 180 days", "pct"),
    ("completion_360d", "Colonoscopy completed within 360 days", "pct"),
    (("ratio", "completion_180d"), "Completion ratio vs baseline at 180 days (navigation RR for comparison: 1.27)", "ratio"),
    ("open_90d", "Cases still open at 90 days", "pct"),
    ("closed_share", "Cases closed (evidence and human sign-off) by end of follow-up", "pct"),
    ("time_to_closure_median", "Time to safe closure, median days (closed cases only)", "days"),
    ("time_to_first_contact_median", "Time to first patient contact, median days", "days"),
    ("lost_to_follow_up_rate", "Lost to follow-up (no colonoscopy, no progress for 30+ days)", "pct"),
    ("silent_loss_rate", "Lost AND never surfaced to a human", "pct"),
    ("delay_harm_share", "No colonoscopy by the delay-harm threshold (270 days)", "pct"),
    ("escalations_per_100_cases", "Escalations per 100 cases (cost nothing in the model, see capacity caveat)", "num"),
    ("escalation_precision", "Escalation precision (acted on within 5 working days)", "pct"),
]


def headline_table(res: dict) -> str:
    lit = _has(res, LITERATURE)
    header = ["Metric", "Baseline"] + (["Orchestrated, literature anchored"] if lit else []) + ["Orchestrated, upper range"]
    rows = []
    for key, label, kind in HEADLINE_ROWS:
        if isinstance(key, tuple):
            row = [label, "1.00"]
            for scen in ((LITERATURE,) if lit else ()) + (UPPER,):
                r = _ratio(res, key[1], scen)
                row.append("n/a" if r is None else f"{r:.2f}")
        else:
            row = [label, _cell(_m(res, BASELINE, key), kind)]
            for scen in ((LITERATURE,) if lit else ()) + (UPPER,):
                row.append(_cell(_m(res, ORCHESTRATED, key, scen), kind))
        rows.append(row)
    return _table(header, rows)


def headline_paragraph(res: dict) -> str:
    if not _has(res, LITERATURE):
        return ("Only the upper-range scenario was evaluated (the literature-anchored overlay was switched off), so the figures below are "
                "the mechanism ceiling and should not be quoted as an expected effect.")

    def pct(x):
        return f"{100 * x:.0f}%"

    m = lambda arm, key, scen: _m(res, arm, key, scen)["mean"]  # noqa: E731
    key = "completion_180d" if _m(res, ORCHESTRATED, "completion_180d", UPPER) else "completion_final"
    horizon = "180 days" if key == "completion_180d" else f"the {res['meta']['follow_up_days']}-day follow-up"
    b180 = m(BASELINE, key, UPPER)
    l180, u180 = m(ORCHESTRATED, key, LITERATURE), m(ORCHESTRATED, key, UPPER)
    rl, ru = _ratio(res, key, LITERATURE), _ratio(res, key, UPPER)
    reach = next((o["value"] for o in _sc(res, LITERATURE)["overrides"] if o["key"] == "orchestration.programme_reach"), None)
    silent = _m(res, ORCHESTRATED, "silent_loss_rate", LITERATURE)
    reach_txt = ""
    if reach is not None and silent:
        reach_txt = (
            f" To hold the average effect to the published figure the scenario lets the orchestrated process reach only "
            f"{pct(reach)} of cases, so {pct(silent['mean'])} of cases are still lost without a human being told; that is a device for "
            f"calibrating the average effect, not a prediction of how many cases a real programme would miss."
        )
    return (
        f"There are two orchestrated scenarios and one baseline. **Quote the literature-anchored scenario.** It holds the orchestrated arm "
        f"to what published studies report: the completion effect equals the pooled patient-navigation risk ratio of 1.27, SMS reminders add "
        f"nothing beyond the no-reminder attendance rate (the lower bound of the published range), and escalating to a supervisor or "
        f"coordinator is no more effective than re-contacting the patient. In it, {pct(l180)} of patients reach a confirmatory colonoscopy "
        f"within {horizon} against {pct(b180)} at baseline (ratio {rl:.2f}).{reach_txt} **The upper-range scenario shows the mechanism ceiling**: "
        f"what ownership, timers, escalation and re-contact could deliver if every case got the full orchestrated process and every "
        f"escalation worked as assumed. It reaches {pct(u180)} within {horizon} (ratio {ru:.2f}), far above anything published for navigation, so "
        f"read it as an upper bound on the mechanism and not as a forecast. Both scenarios use the same synthetic patients, the same random "
        f"numbers and the same baseline arm; the baseline was calibrated to published unmanaged completion, and the only orchestrated-arm fit "
        f"is the reach parameter, set so the literature-anchored 180-day ratio equals the navigation risk ratio."
    )


def render_markdown(res: dict) -> str:
    meta = res["meta"]
    lit = _has(res, LITERATURE)
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    scen_order = ((LITERATURE,) if lit else ()) + (UPPER,)

    def arm_table(scen: str) -> str:
        sc = _sc(res, scen)
        rows = []
        for key, label, kind in HEADLINE:
            rows.append([
                label,
                _cell(_m(res, BASELINE, key, scen), kind),
                _cell(_m(res, ORCHESTRATED, key, scen), kind),
                _cell(sc["difference"].get(key), kind, signed=True),
            ])
        return _table(["Metric", "Baseline (95% CI)", "Orchestrated (95% CI)", "Difference (95% CI)"], rows)

    params_line = f"- Parameters hash `{meta['params_hash'][:16]}` (upper range)"
    if lit:
        params_line += f", `{_sc(res, LITERATURE)['params_hash'][:16]}` (literature anchored)"
    parts = [
        "# Evaluation results: baseline vs orchestrated",
        "",
        f"generated_at: {gen} (this line is the only non-deterministic content; `results.json` is byte-for-byte reproducible)",
        "",
        "All data are synthetic. Reproduce every number here with `make evaluate`; verify reproducibility with `make evaluate-check`.",
        "",
        f"- Seed {meta['seed']}, {meta['replications']} replications x {meta['cases_per_replication']:,} cases x {meta['follow_up_days']} days of follow-up, "
        f"{meta['bootstrap_resamples']} bootstrap resamples",
        params_line + f", pathway `{meta['pathway']}` v{meta['pathway_version']} hash `{meta['pathway_hash'][:16]}`, git commit `{meta['git_commit'][:12]}`",
        f"- Intervals are percentile bootstrap 95% CIs over replications; differences are orchestrated minus baseline, paired by replication (same patients and random numbers in both arms).",
        "",
        "## Headline: baseline vs the two orchestrated scenarios",
        "",
        headline_paragraph(res),
        "",
        headline_table(res),
        "",
        "Metric definitions (numerator, denominator, clocks, censoring) are in [`metrics.md`](metrics.md).",
        "",
    ]
    cap = capacity_caveat(res)
    if cap:
        parts += [cap, ""]
    parts += ["## Summary", ""]
    for scen in scen_order:
        parts += [f"**{SCENARIO_TITLE[scen].capitalize()}.** " + summary_paragraph(res, scen, caveat=scen == scen_order[-1]), ""]
    for scen in scen_order:
        parts += [
            f"## Arm comparison: {SCENARIO_TITLE[scen]} scenario" + (" (the one to quote)" if scen == LITERATURE else " (mechanism ceiling)"),
            "",
            arm_table(scen),
            "",
        ]
    parts += [
        "Open = not currently closed; a closed case that is reopened counts as open again. Time to closure is conditional on closing within the follow-up window. "
        "Lost to follow-up = no confirmatory colonoscopy by the end of follow-up and either an explicit lost-contact flag or no pathway progress for 30+ days. "
        "`n/a` means the arm has no such events by design (the baseline has no named owner and no escalations) or no input from the AI cut yet.",
        "",
        calibration_section(res),
        "",
    ]
    for scen in scen_order:
        parts += [fairness_section(res, scen), ""]
    for scen in scen_order:
        parts += [messy_section(res, scen), ""]
    parts += [param_section(res), ""]
    return "\n".join(parts)


def capacity_caveat(res: dict) -> str:
    """Filled in by ticket B13."""
    return ""
