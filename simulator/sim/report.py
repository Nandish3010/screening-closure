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


def _esc(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _m(res: dict, arm: str, key: str):
    return res["arms"][arm].get(key)


def summary_paragraph(res: dict) -> str:
    meta = res["meta"]
    b, o, d = res["arms"][BASELINE], res["arms"][ORCHESTRATED], res["difference"]

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
    c_key = "completion_180d" if _m(res, ORCHESTRATED, "completion_180d") else "completion_final"
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
            f"{pct(mean(o['silent_loss_rate']))} in the orchestrated arm, where lost or unreachable cases are flagged to a human "
            f"and never closed automatically."
        )
    sc = meta["parameter_status_counts"]
    n_all = sum(sc.values())
    weak = sc.get("assumed", 0) + sc.get("calibrated", 0)
    parts.append(
        f"These results come from a model, not a trial: {weak} of {n_all} parameters are assumed or calibrated rather than "
        f"measured, so the direction of the effect follows from the mechanisms while its size depends on those inputs. "
        f"Rerun with `make evaluate` after changing any parameter in `simulator/params.yaml`."
    )
    return " ".join(parts)


def calibration_section(res: dict) -> str:
    params = {p["key"]: p for p in res["parameters"]}
    tg = {d: params[k]["value"] for d, k in (("90", "colonoscopy_completion_3m"), ("180", "colonoscopy_completion_6m"), ("360", "colonoscopy_completion_12m"))}
    ref = {d: params[k]["value"] for d, k in (("90", "colonoscopy_completion_3m_integrated_ref"), ("180", "colonoscopy_completion_6m_integrated_ref"), ("360", "colonoscopy_completion_12m_integrated_ref"))}
    rows = []
    worst = 0.0
    for d, lab in (("90", "90 days"), ("180", "180 days"), ("360", "360 days")):
        key = f"completion_{d}d"
        bs, os_ = _m(res, BASELINE, key), _m(res, ORCHESTRATED, key)
        if not bs:
            continue
        gap = bs["mean"] - tg[d]
        worst = max(worst, abs(gap))
        rows.append([lab, f"{100 * tg[d]:.1f}%", _cell(bs, "pct"), f"{100 * gap:+.1f}pp",
                     f"{100 * ref[d]:.1f}%", _cell(os_, "pct")])
    txt = ["## Calibration check: does the baseline reproduce published completion?", ""]
    txt.append(
        "The baseline arm is the unmanaged process. Its completion curve was tuned (parameters with status `calibrated`) to the "
        "Mohl et al. 2023 curve for positive stool tests in US health systems. The orchestrated arm is compared with the "
        "actively managed Kaiser Permanente curve (Corley et al. 2017), which is the nearest published ceiling."
    )
    txt.append("")
    txt.append(_table(["Follow-up", "Published unmanaged (Mohl 2023)", "Simulated baseline", "Gap", "Published managed (Corley 2017)", "Simulated orchestrated"], rows))
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
    for k in ("completion_180d",):
        bs, os_ = _m(res, BASELINE, k), _m(res, ORCHESTRATED, k)
        if bs and os_ and nav:
            txt.append(
                f"The simulated orchestrated-to-baseline completion ratio at 180 days is {os_['mean'] / bs['mean']:.2f}, against a pooled "
                f"risk ratio of {nav} for patient navigation in published trials. Orchestration here bundles ownership, timers, escalation and "
                f"human outreach, and the ratio is larger than the navigation figure because the unmanaged baseline loses many cases to "
                f"missed hand-offs that a managed programme prevents; treat it as an upper-range illustration, bounded above by the managed-programme ceiling."
            )
    return "\n".join(txt)


def fairness_section(res: dict) -> str:
    out = ["## Fairness: completion and time to closure by language and channel", ""]
    for dim, title in (("language", "Language"), ("channel", "Preferred contact channel")):
        groups = sorted({k.split(".")[2] for k in res["arms"][BASELINE] if k.startswith(f"fairness.{dim}.") and k.count(".") == 3 and k.endswith(".n")})
        rows = []
        for g in groups:
            row = [g]
            for arm in (BASELINE, ORCHESTRATED):
                row.append(_cell(_m(res, arm, f"fairness.{dim}.{g}.completion_final"), "pct"))
                row.append(_cell(_m(res, arm, f"fairness.{dim}.{g}.closure_median"), "days"))
            rows.append(row)
        out.append(f"### {title}")
        out.append("")
        out.append(_table([title, "Baseline completion", "Baseline median days to closure", "Orchestrated completion", "Orchestrated median days to closure"], rows))
        out.append("")
        gap_rows = []
        for label, key, kind in (("Completion, max gap between groups", f"fairness.{dim}.completion_gap_max", "pct"),
                                 ("Median days to closure, max gap between groups", f"fairness.{dim}.closure_median_gap_max_days", "days")):
            gap_rows.append([label, _cell(_m(res, BASELINE, key), kind), _cell(_m(res, ORCHESTRATED, key), kind),
                             _cell(res["difference"].get(key), kind, signed=True)])
        out.append(_table(["Max-gap statistic (largest minus smallest group)", "Baseline", "Orchestrated", "Difference"], gap_rows))
        out.append("")
    out.append("Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.")
    return "\n".join(out)


def messy_section(res: dict) -> str:
    rows = []
    for tag, label in TAG_LABEL.items():
        n_b = _m(res, BASELINE, f"messy.{tag}.n")
        rows.append([
            label,
            f"{n_b['mean']:.0f}" if n_b else "n/a",
            _cell(_m(res, BASELINE, f"messy.{tag}.handled_rate"), "pct"),
            _cell(_m(res, ORCHESTRATED, f"messy.{tag}.handled_rate"), "pct"),
            _cell(_m(res, BASELINE, f"messy.{tag}.completion_final"), "pct"),
            _cell(_m(res, ORCHESTRATED, f"messy.{tag}.completion_final"), "pct"),
            _cell(_m(res, ORCHESTRATED, f"messy.{tag}.completion_final_unaffected"), "pct"),
        ])
    out = ["## Messy-case handling", ""]
    out.append(_table(["Messy case", "Cases per replication", "Handled explicitly: baseline", "Handled explicitly: orchestrated",
                       "Completion: baseline", "Completion: orchestrated", "Completion: orchestrated, unaffected cases"], rows))
    out.append("")
    out.append("`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, "
               "second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, "
               "so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute.")
    return "\n".join(out)


def param_section(res: dict) -> str:
    sc = res["meta"]["parameter_status_counts"]
    head = ", ".join(f"{v} {k}" for k, v in sc.items())
    rows = []
    for p in res["parameters"]:
        v = p["value"]
        vs = str(v) if not isinstance(v, (dict, list)) else str(v).replace("'", "")
        rows.append([f"`{p['key']}`", _esc(vs), p["status"], _esc(p["source"])])
    return "\n".join([
        "## Parameters", "",
        f"Parameter files: {', '.join('`' + f + '`' for f in res['meta']['params_files'])}. Status counts: {head}. "
        "`sourced` and `sourced-adjacent` values are from public documents (details and caveats in `docs/evidence.md`); "
        "`calibrated` values were tuned to the published baseline curve; `assumed` values have no public source.",
        "",
        _table(["Parameter", "Value", "Status", "Source"], rows),
    ])


def render_markdown(res: dict) -> str:
    meta = res["meta"]
    rows = []
    for key, label, kind in HEADLINE:
        rows.append([
            label,
            _cell(_m(res, BASELINE, key), kind),
            _cell(_m(res, ORCHESTRATED, key), kind),
            _cell(res["difference"].get(key), kind, signed=True),
        ])
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ov = meta.get("sla_overrides") or {}
    sla_note = ""
    if ov:
        sla_note = "SLA override: " + "; ".join(
            f"`{k}` uses {v['used']} working days (DoH KPI, `followup_target_working_days`) instead of {v['pathway_yaml']} in the pathway YAML"
            for k, v in sorted(ov.items())
        ) + ". "
    parts = [
        "# Evaluation results: baseline vs orchestrated",
        "",
        f"generated_at: {gen} (this line is the only non-deterministic content; `results.json` is byte-for-byte reproducible)",
        "",
        "All data are synthetic. Reproduce every number here with `make evaluate`; verify reproducibility with `make evaluate-check`.",
        "",
        f"- Seed {meta['seed']}, {meta['replications']} replications x {meta['cases_per_replication']:,} cases x {meta['follow_up_days']} days of follow-up, "
        f"{meta['bootstrap_resamples']} bootstrap resamples",
        f"- Parameters hash `{meta['params_hash'][:16]}`, pathway `{meta['pathway']}` v{meta['pathway_version']} hash `{meta['pathway_hash'][:16]}`, git commit `{meta['git_commit'][:12]}`",
        f"- {sla_note}Intervals are percentile bootstrap 95% CIs over replications; differences are orchestrated minus baseline, paired by replication (same patients and random numbers in both arms).",
        "",
        "## Summary",
        "",
        summary_paragraph(res),
        "",
        "## Arm comparison",
        "",
        _table(["Metric", "Baseline (95% CI)", "Orchestrated (95% CI)", "Difference (95% CI)"], rows),
        "",
        "Open = not currently closed; a closed case that is reopened counts as open again. Time to closure is conditional on closing within the follow-up window. "
        "Lost to follow-up = no confirmatory colonoscopy by the end of follow-up and either an explicit lost-contact flag or no pathway progress for 30+ days. "
        "`n/a` means the arm has no such events by design (the baseline has no named owner and no escalations) or no input from the AI cut yet.",
        "",
        calibration_section(res),
        "",
        fairness_section(res),
        "",
        messy_section(res),
        "",
        param_section(res),
        "",
    ]
    return "\n".join(parts)
