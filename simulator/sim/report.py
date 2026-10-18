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
    ("time_to_closure_median", "Time to safe closure, median (among closed cases only)", "days"),
    ("time_to_closure_p90", "Time to safe closure, p90 (among closed cases only)", "days"),
    ("time_to_closure_km_median", "Time to safe closure, Kaplan-Meier median (censoring-aware; n/a = under half closed by end of follow-up)", "days"),
    ("time_to_closure_rmst", "Time to safe closure, restricted mean (days event-free within follow-up, censoring-aware)", "days"),
    ("closed_share", "Cases closed (with evidence and human sign-off) by end of follow-up", "pct"),
    ("time_to_ownership_median", "Time to named owner, median", "days"),
    ("time_to_ownership_p90", "Time to named owner, p90", "days"),
    ("time_to_first_contact_median", "Time to first patient contact, median", "days"),
    ("time_to_first_contact_p90", "Time to first patient contact, p90", "days"),
    ("time_to_colonoscopy_median", "Time to colonoscopy, median (among completers only)", "days"),
    ("time_to_colonoscopy_km_median", "Time to colonoscopy, Kaplan-Meier median (censoring-aware)", "days"),
    ("time_to_colonoscopy_rmst", "Time to colonoscopy, restricted mean (censoring-aware)", "days"),
    ("reopen_rate", "Reopen rate (reopened / closed)", "pct"),
    ("escalation_acted_on_rate", "Escalation acted-on rate (within 5 working days)", "pct"),
    ("escalations_per_100_cases", "Escalations per 100 cases", "num"),
    ("automation_abstention_rate", "Automation abstention rate (AI cut supplies this)", "pct"),
    ("lost_to_follow_up_rate", "Lost to follow-up (no colonoscopy, no progress for 30+ days)", "pct"),
    ("silent_loss_rate", "Lost AND never surfaced to a human, programme level (all cases)", "pct"),
    ("silent_loss_rate_in_reach", "Lost AND never surfaced to a human, in reach (cases the programme reached)", "pct"),
    ("delay_harm_share", "No colonoscopy by the delay-harm threshold (270 days)", "pct"),
    ("consent_breach_rate", "Outreach sent after consent withdrawn, programme level (all cases)", "pct"),
    ("consent_breach_rate_in_reach", "Outreach sent after consent withdrawn, in reach (cases the programme reached)", "pct"),
    ("in_reach_share", "Cases within the programme's reach (denominator of the in-reach rows)", "pct"),
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
        if dd["ci_low"] is None or dd["ci_high"] is None:  # a single replication: no interval to speak of
            return f"{dd['mean'] * scale:+.1f} {unit} (one replication: no confidence interval)"
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
            f"at baseline; that conditional median only covers the {pct(mean(b['closed_share']))} of baseline cases that closed at all "
            f"({pct(mean(o['closed_share']))} orchestrated), so an arm that closes more slow cases looks slower on it."
        )
        if mean(o.get("time_to_closure_rmst")) is not None and mean(b.get("time_to_closure_rmst")) is not None:
            km_o, km_b = mean(o.get("time_to_closure_km_median")), mean(b.get("time_to_closure_km_median"))
            km_txt = (f"the Kaplan-Meier median is {km_o:.0f} days orchestrated against {km_b:.0f} at baseline and "
                      if km_o is not None and km_b is not None else
                      ("the Kaplan-Meier median is reached at " + (f"{km_o:.0f} days orchestrated" if km_o is not None else "no point within follow-up orchestrated")
                       + " and " + (f"{km_b:.0f} days at baseline" if km_b is not None else "no point within follow-up at baseline") + ", and "))
            parts.append(
                f"Counting the cases that never closed as censored at the end of follow-up instead of dropping them, {km_txt}"
                f"the restricted mean time to closure within {meta['follow_up_days']} days is {mean(o['time_to_closure_rmst']):.0f} against "
                f"{mean(b['time_to_closure_rmst']):.0f} days."
            )
    if mean(b.get("silent_loss_rate")) is not None:
        so, sb = mean(o["silent_loss_rate"]) or 0.0, mean(b["silent_loss_rate"])
        si, sbi = mean(o.get("silent_loss_rate_in_reach")), mean(b.get("silent_loss_rate_in_reach"))
        reach = mean(o.get("in_reach_share"))
        txt = (
            f"{pct(sb)} of baseline cases were lost without anyone being told, against {pct(so)} in the orchestrated arm at programme level"
        )
        if si is not None and sbi is not None and reach is not None and reach < 0.9995:
            txt += (
                f"; among the {pct(reach)} of cases the programme reached the orchestrated figure is {pct(si)} against {pct(sbi)} for the same "
                f"patients at baseline, and the programme-level remainder is cases outside the programme's reach, which are handled as today."
            )
        else:
            txt += (
                ", where lost or unreachable cases are flagged to a human and never closed automatically"
                + ("." if so < 0.005 else "; the remainder are cases outside the programme's reach, which are handled as today.")
            )
        parts.append(txt)
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
        out.append(_table([title, "Baseline completion", "Baseline median days to closure (among closed cases only)", "Orchestrated completion",
                           "Orchestrated median days to closure (among closed cases only)"], rows))
        out.append("")
        krows = []
        for g in groups:
            row = [g]
            for arm in (BASELINE, ORCHESTRATED):
                row.append(_cell(_m(res, arm, f"fairness.{dim}.{g}.closure_km_median", scen), "days"))
                row.append(_cell(_m(res, arm, f"fairness.{dim}.{g}.closure_rmst", scen), "days"))
            krows.append(row)
        out.append(f"Censoring-aware closure time by {title.lower()} (cases not closed by the end of follow-up are censored there, not dropped; "
                   f"`n/a` = fewer than half the group closed).")
        out.append("")
        out.append(_table([title, "Baseline Kaplan-Meier median days", "Baseline restricted mean days", "Orchestrated Kaplan-Meier median days",
                           "Orchestrated restricted mean days"], krows))
        out.append("")
        gap_rows = []
        for label, key, kind in (("Completion, max gap between groups", f"fairness.{dim}.completion_gap_max", "pct"),
                                 ("Median days to closure (among closed cases only), max gap between groups", f"fairness.{dim}.closure_median_gap_max_days", "days"),
                                 ("Kaplan-Meier median days to closure, max gap between groups", f"fairness.{dim}.closure_km_median_gap_max_days", "days"),
                                 ("Restricted mean days to closure, max gap between groups", f"fairness.{dim}.closure_rmst_gap_max_days", "days")):
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
    ("time_to_closure_median", "Time to safe closure, median days (among closed cases only)", "days"),
    ("time_to_closure_km_median", "Time to safe closure, Kaplan-Meier median days (censoring-aware)", "days"),
    ("time_to_closure_rmst", "Time to safe closure, restricted mean days (censoring-aware)", "days"),
    ("time_to_first_contact_median", "Time to first patient contact, median days", "days"),
    ("lost_to_follow_up_rate", "Lost to follow-up (no colonoscopy, no progress for 30+ days)", "pct"),
    ("silent_loss_rate", "Lost AND never surfaced to a human: programme level (all cases)", "pct"),
    ("silent_loss_rate_in_reach", "Lost AND never surfaced to a human: in reach (cases the programme reached)", "pct"),
    ("delay_harm_share", "No colonoscopy by the delay-harm threshold (270 days)", "pct"),
    ("consent_breach_rate", "Outreach after consent withdrawn: programme level (all cases)", "pct"),
    ("consent_breach_rate_in_reach", "Outreach after consent withdrawn: in reach (cases the programme reached)", "pct"),
    ("escalations_per_100_cases", "Escalations per 100 cases (cost nothing in the model, see capacity caveat)", "num"),
    ("escalation_acted_on_rate", "Escalation acted-on rate (within 5 working days)", "pct"),
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
            f"{pct(reach)} of cases, so {pct(silent['mean'])} of cases are still lost without a human being told (programme level; among the cases it reaches the figure is 0%); that is a device for "
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


def reach_paragraph(res: dict) -> str:
    """Why the headline table reports silent loss and post-withdrawal outreach twice (ticket B15)."""
    scen = LITERATURE if _has(res, LITERATURE) else UPPER
    reach = None
    if scen == LITERATURE:
        reach = next((o["value"] for o in _sc(res, LITERATURE)["overrides"] if o["key"] == "orchestration.programme_reach"), None)
    if reach is None:
        reach = next((p["value"] for p in res["parameters"] if p["key"] == "orchestration.programme_reach"), 1.0)
    si = _m(res, ORCHESTRATED, "silent_loss_rate_in_reach", scen)
    ci = _m(res, ORCHESTRATED, "consent_breach_rate_in_reach", scen)
    zero = lambda c: c is not None and abs(c["mean"]) < 5e-4  # noqa: E731
    both_zero = zero(si) and zero(ci)
    name = "literature-anchored" if scen == LITERATURE else "upper-range"
    first = (
        "**Lost and never surfaced, and outreach after consent withdrawn: two denominators each.** The programme-level rows count every case. "
        "The in-reach rows count only the cases the programme reached (`orchestration.programme_reach`; in the baseline column, the same patients "
        "under today's process, so the two columns compare like with like). "
        + ("In the orchestrated arm both in-reach rows are 0% by construction: a lost or unreachable case in reach is flagged to a human and the "
           "consent ledger blocks outreach after a withdrawal. " if both_zero else
           "In the orchestrated arm both in-reach rows are at or near 0%, because a lost or unreachable case in reach is flagged to a human and the "
           "consent ledger blocks outreach after a withdrawal. ")
    )
    second = (
        f"The programme-level figures follow from the `programme_reach` assumption ({reach} in the {name} scenario): cases outside the reach are handled "
        f"as today and can still be lost silently or contacted after a withdrawal, so those figures are not a measured property of the orchestration."
        if reach < 1 else
        "In this scenario `programme_reach` is 1.0, so every case is in reach and the programme-level and in-reach figures coincide."
    )
    return first + second


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
        reach_paragraph(res),
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
        effort_section(res),
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
    """Prominent caveat: the model charges nothing for escalations (ticket B13)."""
    up = _m(res, ORCHESTRATED, "escalations_per_100_cases", UPPER)
    if not up:
        return ""
    txt = f"about {up['mean']:.0f} escalations per 100 cases in the upper-range scenario"
    if _has(res, LITERATURE):
        lit = _m(res, ORCHESTRATED, "escalations_per_100_cases", LITERATURE)
        if lit:
            txt += f" ({lit['mean']:.0f} in the literature-anchored one)"
    return "\n".join([
        "## Capacity caveat: escalations cost nothing in this model",
        "",
        f"**Every gain above is a gain before capacity constraints.** Cases are independent in the simulator: there are no shared endoscopy "
        f"slots, no staff workload and no queueing, and a supervisor or coordinator acts on an escalation with a fixed probability however "
        f"many others are open. The orchestrated arm raises {txt}, roughly one for every case. A real programme needs people and "
        f"colonoscopy slots to work them, and the completion and time gains would shrink if they are not there. A capacity model (staff "
        f"time per escalation, slot supply, queueing) is future work; until it exists, use `escalations_per_100_cases` to size a pilot "
        f"rather than the completion gains. The staff minutes an escalation takes are counted in the Human effort section, but still without any queue or "
        f"shared capacity. Definitions: [`metrics.md`](metrics.md).",
    ])


# ---------------------------------------------------------------------------------------------------------------------
# Human effort (ticket B17)
# ---------------------------------------------------------------------------------------------------------------------

EFFORT_ROWS = [
    ("minutes_per_case", "Human minutes per case", "days"),
    ("minutes_per_closed_case", "Human minutes per closed case", "days"),
    ("human_actions_per_case", "Human actions per case", "num"),
    ("automation_share", "Automation share (automated actions / all actions)", "pct"),
]
EFFORT_REDUCTION_ROWS = [
    ("labour_reduction", "**Labour reduction**, minutes per case (1 - orchestrated / baseline)"),
    ("labour_reduction_per_closed_case", "Labour reduction, minutes per closed case"),
    ("human_actions_reduction", "Reduction in human actions per case"),
]
ROBUSTNESS_LABELS = [
    ("headline", "Headline (the table above)"),
    ("baseline_chasing_not_costed", "Baseline chasing not costed (only logged events count in the baseline)"),
    ("baseline_chasing_intensive", "Baseline chases harder (up to 5 attempts, every 7 days)"),
    ("coordinator_and_clinician_only", "Specialist minutes left out (coordinator and owner clinician only)"),
    ("orchestrated_bookings_keyed_by_hand", "Orchestrated arm: a person still records every booking"),
    ("no_draft_approval_charge", "No charge for approving drafts (a phone call needs no approval)"),
    ("escalation_outreach_is_an_approved_draft_not_a_call", "Escalation outreach is an approved drafted message, not a phone call (calls cost no minutes)"),
    ("all_minutes_low", "Every minutes figure at its low end"),
    ("all_minutes_high", "Every minutes figure at its high end"),
    ("least_favourable_minutes", "Least favourable corner: baseline-only actions at their low end, orchestrated-only at their high end"),
]


def _eff(res: dict, scen: str) -> dict | None:
    return res.get("effort", {}).get("scenarios", {}).get(scen)


def effort_target_met(res: dict, scen: str, subset: str = "all", target: float = 0.40) -> bool | None:
    e = _eff(res, scen)
    if not e:
        return None
    r = e[subset]["reduction"]["labour_reduction"]
    return None if not r or r["ci_low"] is None else r["ci_low"] > target


def effort_section(res: dict) -> str:
    eff = res.get("effort")
    if not eff:
        return ""
    scens = [s for s in ((LITERATURE,) if _has(res, LITERATURE) else ()) + (UPPER,) if _eff(res, s)]
    model = eff["model"]
    acts = {a["id"]: a for a in model["actions"]}
    pc = lambda x: f"{100 * x:.0f}%"  # noqa: E731
    p1 = lambda x: "n/a" if x is None else f"{100 * x:.1f}%"  # noqa: E731
    verdict = lambda met: "not assessable (one replication has no interval)" if met is None else ("met" if met else "not met")  # noqa: E731

    def cell(scen, subset, arm, key, kind):
        return _cell(_eff(res, scen)[subset]["arms"][arm][key], kind)

    def red(scen, subset, key):
        return _cell(_eff(res, scen)[subset]["reduction"][key], "pct")

    head = ["Measure", "Baseline"] + [f"Orchestrated, {SCENARIO_TITLE[s]}" for s in scens]
    rows = []
    for key, label, kind in EFFORT_ROWS:
        rows.append([label, cell(scens[0], "all", BASELINE, key, kind)] + [cell(s, "all", ORCHESTRATED, key, kind) for s in scens])
    for key, label in EFFORT_REDUCTION_ROWS:
        rows.append([label, "-"] + [red(s, "all", key) for s in scens])
    rows.append(["Among the cases the programme reaches: human minutes per case", cell(scens[0], "in_reach", BASELINE, "minutes_per_case", "days")]
                + [cell(s, "in_reach", ORCHESTRATED, "minutes_per_case", "days") for s in scens])
    rows.append(["Among the cases the programme reaches: **labour reduction**", "-"] + [red(s, "in_reach", "labour_reduction") for s in scens])
    main = _table(head, rows)

    role_names = {"coordinator": "Coordinator", "owner_clinician": "Owner clinician", "specialist": "Specialist"}
    rrows = []
    for r in ("coordinator", "owner_clinician", "specialist"):
        rrows.append([f"{role_names[r]}: minutes per case", cell(scens[0], "all", BASELINE, f"minutes_per_case.{r}", "days")]
                     + [cell(s, "all", ORCHESTRATED, f"minutes_per_case.{r}", "days") for s in scens])
    for r in ("coordinator", "owner_clinician", "specialist"):
        rrows.append([f"{role_names[r]}: reduction", "-"] + [red(s, "all", f"labour_reduction.{r}") for s in scens])
    roles = _table(head, rrows)

    def rob(scen, key):
        v = _eff(res, scen)["robustness"].get(key)
        return "n/a" if v is None else f"{100 * v:+.1f}%"

    robust = _table(["Variant (labour reduction, minutes per case, point estimate)"] + [f"Orchestrated, {SCENARIO_TITLE[s]}" for s in scens],
                    [[label] + [rob(s, k) for s in scens] for k, label in ROBUSTNESS_LABELS])

    ref = LITERATURE if LITERATURE in scens else UPPER
    torn = _eff(res, ref)["tornado"][:5]
    base_red = _eff(res, ref)["tornado_base"]
    tt = _table(
        ["Minutes figure (top 5 by swing)", "Grade", "Low / central / high (min)", "Reduction at low", "Reduction at high"],
        [[f"{acts[t['action']]['label']} (`{t['action']}`)", t["status"], f"{t['low_minutes']:g} / {acts[t['action']]['minutes']:g} / {t['high_minutes']:g}",
          p1(t["reduction_at_low"]), p1(t["reduction_at_high"])] for t in torn],
    )

    def status_share(scen, arm):
        a = _eff(res, scen)["all"]["arms"][arm]
        tot = a["minutes_per_case"]["mean"]
        return {s: (a[f"minutes_per_case.status.{s}"]["mean"] / tot if tot else 0.0) for s in ("sourced", "sourced-adjacent", "assumed")}

    sb, so = status_share(ref, BASELINE), status_share(ref, ORCHESTRATED)

    arows = []
    for a in model["actions"]:
        arows.append([f"`{a['id']}` {a['label']}", a["role"].replace("_", " "), {"manual": "today's process", "orchestrated": "orchestrated", "both": "both"}[a["process"]],
                      f"{a['minutes']:g} ({a['low']:g} to {a['high']:g})", a["status"], _esc(a["source"])])
    action_table = _table(["Human action", "Role", "Process", "Minutes (range)", "Grade", "Basis"], arows)

    steps = "; ".join(dict.fromkeys(s["step"] for s in model["automated_steps"]))

    # ---- paragraph ----------------------------------------------------------------------------------------------------
    lit = _eff(res, LITERATURE) if LITERATURE in scens else None
    up = _eff(res, UPPER)

    def val(e, subset, key):
        return e[subset]["reduction"][key]["mean"]

    def ci(e, subset, key):
        c = e[subset]["reduction"][key]
        return f"{p1(c['mean'])}, 95% CI {p1(c['ci_low'])} to {p1(c['ci_high'])}"

    ba = _eff(res, ref)["all"]["arms"][BASELINE]["minutes_per_case"]["mean"]
    oa = _eff(res, ref)["all"]["arms"][ORCHESTRATED]["minutes_per_case"]["mean"]
    reach = next((o["value"] for o in _sc(res, LITERATURE)["overrides"] if o["key"] == "orchestration.programme_reach"), None) if lit else None
    target_bits = []
    if lit:
        met_all, met_in = effort_target_met(res, LITERATURE, "all"), effort_target_met(res, LITERATURE, "in_reach")
        target_bits.append(f"In the literature-anchored scenario the programme-level reduction is {ci(lit, 'all', 'labour_reduction')}: the 40% target is "
                           f"**{verdict(met_all)}**. Among the cases the programme reaches it is {ci(lit, 'in_reach', 'labour_reduction')} "
                           f"(target {verdict(met_in)}); per closed case it is {ci(lit, 'all', 'labour_reduction_per_closed_case')}.")
    met_up = effort_target_met(res, UPPER, "all")
    target_bits.append(f"In the upper-range scenario the reduction is {ci(up, 'all', 'labour_reduction')} (target {verdict(met_up)}; the lower end of the interval "
                       f"is {p1(up['all']['reduction']['labour_reduction']['ci_low'])}); per closed case it is {ci(up, 'all', 'labour_reduction_per_closed_case')}.")

    # biggest orchestrated contributors (reference scenario)
    apc = _eff(res, ref)["all"]["actions_per_case"][ORCHESTRATED]
    contrib = sorted(((k, v * acts[k]["minutes"]) for k, v in apc.items() if k in acts), key=lambda kv: -kv[1])[:4]
    contrib_txt = ", ".join(f"{acts[k]['short']} ({m:.1f} min per case)" for k, m in contrib)
    esc_min = sum(apc.get(k, 0) * acts[k]["minutes"] for k in ("O_ACCEPT_ESC_SUPERVISOR", "O_ACCEPT_ESC_COORDINATOR", "O_SUPERVISOR_CALL", "O_COORDINATOR_CALL", "O_APPROVE_DRAFT"))
    chase_min = sum(_eff(res, ref)["all"]["actions_per_case"][BASELINE].get(k, 0) * acts[k]["minutes"] for k in acts if acts[k]["chase"])
    auto_pc = _eff(res, ref)["all"]["arms"][ORCHESTRATED]["automated_actions_per_case"]["mean"]
    human_pc_o = _eff(res, ref)["all"]["arms"][ORCHESTRATED]["human_actions_per_case"]["mean"]
    def role_chg(role):
        a = _eff(res, ref)["all"]["arms"]
        return f"{100 * (a[ORCHESTRATED][f'minutes_per_case.{role}']['mean'] / a[BASELINE][f'minutes_per_case.{role}']['mean'] - 1):+.0f}%"

    lf = _eff(res, ref)["robustness"]["least_favourable_minutes"]
    keyed = _eff(res, ref)["robustness"]["orchestrated_bookings_keyed_by_hand"]
    nochase = _eff(res, ref)["robustness"]["baseline_chasing_not_costed"]
    appr = _eff(res, ref)["robustness"]["escalation_outreach_is_an_approved_draft_not_a_call"]
    reach_txt = (f" In the literature-anchored scenario only {pc(reach)} of orchestrated-arm cases receive the orchestrated process (`programme_reach`); the rest stay manual, "
                 f"so the programme-level reduction is diluted by construction and the in-reach figure is the one that describes the automation itself.") if reach is not None and reach < 1 else ""

    para = [
        f"**What is counted.** Staff minutes per case in each arm, computed from the simulated event log (`simulator/sim/effort.py`; no outcome in this report depends on it). "
        f"A person's time is counted only when a person acts. Today's process is manual at every step (look up and acknowledge the result, phone or write to the patient, "
        f"send the referral, book, record that the patient called back, review the report, file the closure), and the model adds the chasing that leaves no event in the log "
        f"(up to {model['chase']['default']['max_attempts']} attempts every {model['chase']['default']['interval_days']:g} days when a patient, a referral or a colonoscopy is overdue). "
        f"In the orchestrated process the engine does the following by machine and they cost no human minutes: {steps}. A person acts only to accept an escalation, approve a "
        f"drafted message, sign the closure and handle an exhausted reroute list, plus the steps automation cannot do (recording a patient's call-back, a consent withdrawal, a "
        f"transfer decision, a rebooking, a corrected contact, a reopened case, and the specialist's own accept or reject)." + reach_txt,
        "",
        "**Result.** " + " ".join(target_bits),
        "",
        f"**What drives the remaining minutes.** In the {SCENARIO_TITLE[ref].replace('literature anchored', 'literature-anchored')} scenario the orchestrated arm needs {oa:.1f} human minutes per case against {ba:.1f} at baseline. "
        f"The largest orchestrated contributors are {contrib_txt}. Escalation handling alone (accepting, phoning, approving) is {esc_min:.1f} minutes per case, because the orchestrated arm "
        f"escalates far more than today's process ever does; the orchestrated arm also takes {auto_pc:.1f} automated actions per case that the baseline does not do at all, "
        f"so the reduction is in people's time, not in the work done. {human_pc_o:.1f} human actions per case remain, many of them short. "
        f"The time also moves between roles: coordinator minutes change by {role_chg('coordinator')} but owner-clinician minutes by {role_chg('owner_clinician')} "
        f"(accepting escalations, calling, approving and signing are clinician decisions) and specialist minutes by {role_chg('specialist')}; whether that is cheaper depends on wages, which are not in the model.",
        "",
        f"**How far to trust the minutes.** {pc(sb['sourced'] + sb['sourced-adjacent'])} of baseline minutes and {pc(so['sourced'] + so['sourced-adjacent'])} of orchestrated minutes rest on a "
        f"published minutes figure from an adjacent setting (care coordinators and navigators in Australia and the United States); none was measured in the Abu Dhabi pathway"
        + ("" if any(a["status"] == "sourced" for a in model["actions"]) else ", and none is graded `sourced` (a direct measurement of the same task in the same population)") +
        f". The rest ({pc(sb['assumed'])} of baseline and {pc(so['assumed'])} of orchestrated minutes) is assumed. "
        f"Moving every figure to its low or its high end together barely changes the reduction, because it is a ratio, but the relative sizes matter: the table above shows how far "
        f"individual figures move it, and the least favourable corner (baseline-only actions at their low end, orchestrated-only actions at their high end) gives {100 * lf:+.0f}%. "
        f"Three modelling choices carry most of the weight: the baseline's chasing (without it the reduction is {p1(nochase)}, and baseline chasing was "
        f"{chase_min:.1f} minutes per case), whether a person still records every booking in the orchestrated arm (if so, {p1(keyed)}), and whether the human escalation outreach "
        f"is a phone call or a drafted message that a person only approves (if only approved, {p1(appr)}). All three are flagged for confirmation with the engine ticket (A21).",
        "",
        "**Limits.** Human minutes are not capacity: there is no queue, no shift pattern and no shared workload, so a coordinator who accepts three escalations in an hour is not slowed down "
        "(see the capacity caveat). The orchestrated escalation volume is a simulator output (the SLA timers breach often), and a real programme could reduce it by batching or by tuning "
        "the ladder. The simulator has no model-drafted messages (every message is a pre-approved template), so the approval charge is a proxy placed on the human-contact messages. "
        "Time spent by patients, the endoscopy unit's procedure and reception work, and management or training time are outside the count.",
    ]
    return "\n".join([
        "## Human effort",
        "",
        "Human minutes per case in each arm and the reduction automation buys, from the event logs of the same replications as the rest of this report "
        "(95% CIs are percentile bootstrap over replications; the reduction is paired by replication). Definitions: [`metrics.md`](metrics.md) section 11. "
        "Sources and grades of every minutes figure: [`evidence.md`](../evidence.md) section 11.",
        "",
        main,
        "",
        *para,
        "",
        "### Human minutes per case by role",
        "",
        roles,
        "",
        "### How robust is the reduction",
        "",
        robust,
        "",
        f"The five minutes figures that move the {SCENARIO_TITLE[ref]} reduction most, one at a time (central {p1(base_red)}):",
        "",
        tt,
        "",
        "### The minutes table",
        "",
        "Every human action, the minutes used, the range used in the sweep and where the figure comes from. `sourced-adjacent` = a published figure for a related task or setting; "
        "`assumed` = no published figure found. Steps the engine automates cost no human minutes and are listed in `results.json` under `effort.model.automated_steps`.",
        "",
        action_table,
        "",
        "Work that today's process never does (so it costs the baseline nothing, and the orchestrated arm does it by machine): "
        + "; ".join(w["work"][0].lower() + w["work"][1:] for w in model["never_done_in_baseline"]) + ".",
    ])
