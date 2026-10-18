"""sensitivity.md and sensitivity.svg, rendered from the sweep result (the contents of sensitivity.json) and nothing else."""

from __future__ import annotations

from xml.sax.saxutils import escape

TOP_N_CHART = 20
# One-line plain-English meaning of the parameters that tend to matter; a parameter without an entry is shown by key only.
GLOSS = {
    "orchestration.programme_reach": "share of orchestrated-arm cases that actually get the orchestrated process",
    "patients.refusal_prob": "share of patients who will not have the colonoscopy whatever is done",
    "patient_response.self_presentation_prob": "share of patients who eventually act without being prompted",
    "patient_response.self_presentation_median_days": "how long unprompted patients take to act",
    "patient_response.response_rate_by_channel.sms": "chance a patient acts on one SMS",
    "patient_response.response_rate_by_channel.whatsapp": "chance a patient acts on one WhatsApp message",
    "patient_response.response_rate_by_channel.phone": "chance a patient acts on one phone call",
    "patient_response.response_rate_by_channel.letter": "chance a patient acts on one letter",
    "patient_response.language_mismatch_multiplier": "response penalty when the message is not in the patient's language",
    "wrong_contact_rate": "share of patients whose contact details are wrong",
    "staff.forget_prob": "chance a staff step is never done unless prompted",
    "appointment.booking_lead_median_days": "median days from deciding to book to the appointment",
    "baseline.noshow_self_rebook_prob": "baseline: chance a patient who missed an appointment rebooks alone",
    "handoff.baseline_rejection_noticed_prob": "baseline: chance a rejected referral is ever noticed",
    "patients.dropout_prob_per_lapse": "chance a patient disengages after each unanswered contact or no-show",
    "referral_rejection_rate": "share of referrals the receiving service rejects",
    "sms_reminder_effect": "relative cut in no-shows from an SMS reminder",
    "orchestration.escalation_effect_prob.owner": "chance the owner acts on a reminder",
    "orchestration.contact_recovery_prob.on_failure": "chance a wrong contact is corrected when delivery fails",
    "orchestration.watchdog_effect_prob": "chance the coordinator acts on an idle-case flag",
    "metrics.ltfu_idle_days": "days without progress after which a case counts as lost",
    "handoff.response_delay_median_days": "median days the receiving service takes to answer a referral",
    "handoff.max_attempts": "referral attempts before giving up",
    "handoff.response_sla_working_days": "working days before an unanswered referral bounces back to the owner",
    "staff.delay_sigma": "spread of staff delays; a larger value means a longer tail of slow steps",
    "patient_response.response_delay_sigma": "spread of patient response times",
    "patients.language_share.ar": "share of patients whose language is Arabic (the mix is renormalised)",
    "staff.delay_median_days.book_confirmatory": "median days staff take to book the colonoscopy",
    "staff.delay_median_days.contact_patient": "median days staff take to contact the patient",
}


def _pp(x: float, signed: bool = True) -> str:
    return f"{100 * x:+.1f}" if signed else f"{100 * x:.1f}"


def _v(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_v(x) for x in v) + "]"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _end(e: str) -> str:
    return "low end" if e == "low" else "high end"


def _gloss(key: str) -> str:
    g = GLOSS.get(key)
    return f" ({g})" if g else ""


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _esc(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def reading(res: dict) -> list[str]:
    meta, base, summ, rows = res["meta"], res["base"], res["summary"], res["parameters"]
    by_key = {r["key"]: r for r in rows}
    bd = base["metrics"]["completion_180d"]["difference"]
    lo, hi = summ["headline_difference_min"], summ["headline_difference_max"]
    out = []
    out.append(
        f"The headline is the gain in 180-day colonoscopy completion from the orchestrated process over today's process, in the "
        f"literature-anchored scenario: **{_pp(bd)} percentage points** in the base case. Each of the {meta['n_swept']} parameters with a plausible "
        f"range was moved to the low and to the high end of it, one at a time, with every other parameter held at the base case."
    )
    items = []
    for i, k in enumerate(summ["top5"], start=1):
        r = by_key[k]
        a, b = r["low"]["metrics"]["completion_180d"]["difference"], r["high"]["metrics"]["completion_180d"]["difference"]
        items.append(
            f"{i}. `{k}`{_gloss(k)}: swing {_pp(r['swing']['completion_180d'], False)} points, from {_pp(a)} at its low end "
            f"({_v(r['low']['value'])}) to {_pp(b)} at its high end ({_v(r['high']['value'])}); base value {_v(r['base_value'])}."
        )
    out.append("**Which five parameters dominate.** In order of swing in the 180-day completion difference:\n\n" + "\n".join(items))
    n_pts = meta["n_points"] - 1
    fl = summ["flips"]["completion_180d"]
    if not fl:
        flip = (
            f"**Does the direction ever flip? No.** In all {n_pts} one-at-a-time runs the orchestrated arm completes more colonoscopies within 180 days "
            f"than baseline. The smallest gain is {_pp(lo['difference'])} points (`{lo['key']}` at its {_end(lo['end'])})."
        )
    else:
        worst = ", ".join(f"`{f['key']}` at its {_end(f['end'])} ({_pp(f['difference'])})" for f in fl[:5])
        flip = (
            f"**Does the direction ever flip? Yes.** In {len(fl)} of {n_pts} runs the orchestrated arm completes fewer colonoscopies within 180 days "
            f"than baseline: {worst}{' and others' if len(fl) > 5 else ''}."
        )
    if "orchestration.programme_reach" in summ["top5"]:
        out.append("`orchestration.programme_reach` is the parameter that was fitted so the literature-anchored scenario reproduces the published navigation "
                    "effect (ratio 1.27), so its place at or near the top is built in: the headline scales with how many cases the programme reaches, and the "
                    "published average effect alone cannot separate that share from the size of the effect on the cases that are reached.")
    other = []
    for m, label in (("open_90d", "the share of cases still open at 90 days"), ("silent_loss_rate", "silent loss (programme level)")):
        f2 = summ["flips"][m]
        other.append(f"{label}: " + ("never worse than baseline in any run" if not f2 else f"worse than baseline in {len(f2)} runs"))
    flip += " The other two outcomes agree on direction: " + "; ".join(other) + "."
    out.append(flip)
    out.append(
        f"**Range of the headline difference.** Across all sweeps the 180-day completion difference runs from {_pp(lo['difference'])} points "
        f"(`{lo['key']}`, {_end(lo['end'])}) to {_pp(hi['difference'])} points (`{hi['key']}`, {_end(hi['end'])}), against {_pp(bd)} in the base case. "
        f"Between replications the base case itself varies by a standard deviation of {_pp(base['headline_difference_sd_across_replications'], False)} points "
        f"(standard error {_pp(base['headline_difference_se'], False)}), so swings much smaller than that are noise."
    )
    out.append(
        "**How far to trust this.** It is one-at-a-time: interactions between parameters are not explored. Where no published range exists the range "
        "is a mechanical rule (plus or minus 50 percent for rates, halved and doubled for durations; see Method), not evidence, so a parameter that tops "
        "the chart because its rule-made range is wide has not been shown to matter in the world. Calibrated parameters are moved without "
        "recalibrating the baseline, so their swings include moving the baseline away from the published completion curve. The ranking says "
        "which assumptions the result leans on and so where evidence is worth buying first; it is not a forecast."
    )
    return out


def render_markdown(res: dict) -> str:
    meta, base, rows = res["meta"], res["base"], res["parameters"]
    bm = base["metrics"]
    parts = [
        "# Parameter sensitivity: how much do the results lean on each assumption?",
        "",
        "Generated by `make sensitivity` from [`sensitivity.json`](sensitivity.json); do not edit by hand. Reproduce with `make sensitivity`, verify with "
        "`make sensitivity-check`. All data are synthetic.",
        "",
        f"- Design: {meta['design']}. Base case: the {meta['scenario'].replace('_', ' ')} scenario (`{'`, `'.join(meta['params_files'])}`), "
        f"parameter-and-range hash `{meta['params_hash'][:16]}`.",
        f"- Seed {meta['seed']}; {meta['replications']} replications x {meta['cases_per_replication']:,} cases x {meta['follow_up_days']} days of follow-up at every point; "
        f"{meta['n_swept']} parameters, {meta['n_points']} points (base plus low and high for each). The same patients and random numbers are used at every point, "
        f"so differences between points come from the parameter, not from sampling.",
        f"- Base case: baseline {_pp(bm['completion_180d']['baseline'], False)}% and orchestrated {_pp(bm['completion_180d']['orchestrated'], False)}% complete within 180 days "
        f"(difference {_pp(bm['completion_180d']['difference'])} points); {_pp(bm['open_90d']['baseline'], False)}% against {_pp(bm['open_90d']['orchestrated'], False)}% still open at 90 days; "
        f"silent loss {_pp(bm['silent_loss_rate']['baseline'], False)}% against {_pp(bm['silent_loss_rate']['orchestrated'], False)}%. "
        f"These come from {meta['replications']} x {meta['cases_per_replication']:,} cases, so they differ slightly from the 20 x 2,000 figures in [`results.md`](results.md).",
        "",
        "## Reading",
        "",
    ]
    for para in reading(res):
        parts += [para, ""]
    parts += [
        "## Tornado",
        "",
        f"![Tornado chart of the {TOP_N_CHART} parameters with the largest swing in the 180-day completion difference](sensitivity.svg)",
        "",
        f"The chart shows the top {TOP_N_CHART} of {meta['n_swept']}. Each bar runs from the headline difference with the parameter at its low end to the difference with it at its high end, "
        f"through the base case (dashed line).",
        "",
        f"Sorted by the swing in the 180-day completion difference (orchestrated minus baseline, percentage points). Swing = {meta['swing_definition']}. "
        "The silent-loss and open-at-90 columns are the same swing computed on those outcomes (silent loss at programme level, all cases).",
        "",
    ]
    table = []
    for i, r in enumerate(rows, start=1):
        def c(m, e, r=r):
            return r[e]["metrics"][m]["difference"]

        table.append([
            str(i), f"`{r['key']}`", r["status"], _v(r["base_value"]), f"{_v(r['low']['value'])} to {_v(r['high']['value'])}",
            _pp(c("completion_180d", "low")), _pp(c("completion_180d", "high")),
            _pp(r["swing"]["completion_180d"], False), _pp(r["swing"]["silent_loss_rate"], False), _pp(r["swing"]["open_90d"], False),
        ])
    parts += [
        _table(["#", "Parameter", "Status", "Base", "Low to high", "Difference at low (pp)", "Difference at high (pp)",
                "Swing, 180-day completion (pp)", "Swing, silent loss (pp)", "Swing, open at 90 days (pp)"], table),
        "",
        "## Method",
        "",
        "- **Which parameters.** Every parameter with status `assumed` or `calibrated`, and every sourced parameter whose source states a range, carries `range: [low, high]` "
        "and a `range_basis` in `simulator/params.yaml` (or in `simulator/params.conservative.yaml` for the keys that overlay overrides, so the range sits around the "
        "literature-anchored value). Other parameters are listed under Not swept.",
        "- **Where a range comes from.** A `range_basis` starting with \"stated range:\" is quoted from the parameter's source. Otherwise it is one of these default rules, "
        "applied to the base value:",
    ]
    for k, v in meta["default_rules"].items():
        parts.append(f"  - {k}: {v.replace('default rule: ', '')}.")
    parts += [
        "- **Swing.** " + meta["swing_definition"] + ". The tornado is sorted by this swing for `completion_180d`.",
        "- **Not an estimate of uncertainty.** The swings say how much the headline moves when one input moves across a range; they are not confidence intervals. "
        "The headline's own sampling uncertainty is in the replication spread quoted above and in `results.md`.",
        "",
        "## Not swept",
        "",
        "Parameters that carry no range, with the reason. Sourced parameters without a stated range are not swept because inventing a range for a sourced value would "
        "pass a rule of thumb off as evidence; several are benchmarks that the simulator does not read (completion targets, reference curves, age limits).",
        "",
        _table(["Parameter", "Status", "Reason"], [[f"`{n['key']}`", n["status"], _esc(n["reason"])] for n in res["not_swept"]]),
        "",
    ]
    return "\n".join(parts)


# ---- SVG -----------------------------------------------------------------------------------------------------

def _nice_step(span: float) -> float:
    for s in (0.5, 1, 2, 5, 10, 20):
        if span / s <= 8:
            return s
    return 50


def render_svg(res: dict) -> str:
    rows = res["parameters"][:TOP_N_CHART]
    base = 100 * res["base"]["metrics"]["completion_180d"]["difference"]
    vals = [base]
    for r in rows:
        vals += [100 * r["low"]["metrics"]["completion_180d"]["difference"], 100 * r["high"]["metrics"]["completion_180d"]["difference"]]
    lo_v, hi_v = min(vals), max(vals)
    pad = max((hi_v - lo_v) * 0.18, 1.0)
    step = _nice_step(hi_v - lo_v + 2 * pad)
    x0v = (int((lo_v - pad) // step)) * step
    x1v = (int((hi_v + pad) // step) + 1) * step
    W, left, right, top, rh = 980, 380, 40, 92, 26
    plot_w = W - left - right
    H = top + rh * len(rows) + 56

    def X(v: float) -> float:
        return left + (v - x0v) / (x1v - x0v) * plot_w

    BLUE, ORANGE, INK, MUTED, GRID = "#2b6cb0", "#c05621", "#1b1f24", "#4a5568", "#d9dee5"
    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-labelledby="t d" font-family="system-ui, -apple-system, Segoe UI, Helvetica, Arial, sans-serif">',
        '<title id="t">Tornado chart: sensitivity of the 180-day completion gain to each parameter</title>',
        f'<desc id="d">Horizontal bars for the {len(rows)} parameters with the largest swing. Each bar spans the orchestrated-minus-baseline difference in 180-day '
        f'colonoscopy completion, in percentage points, with the parameter at the low and high end of its range, around the base case of {base:+.1f} points.</desc>',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="16" y="26" font-size="16" font-weight="600" fill="{INK}">Sensitivity of the 180-day completion gain (orchestrated minus baseline)</text>',
        f'<text x="16" y="46" font-size="12" fill="{MUTED}">Literature-anchored scenario, one parameter at a time; top {len(rows)} of {res["meta"]["n_swept"]} by swing. Synthetic data.</text>',
        f'<rect x="{left}" y="60" width="12" height="12" fill="{BLUE}"/><text x="{left + 18}" y="71" font-size="12" fill="{INK}">parameter at the low end of its range</text>',
        f'<rect x="{left + 250}" y="60" width="12" height="12" fill="{ORANGE}"/><text x="{left + 268}" y="71" font-size="12" fill="{INK}">parameter at the high end of its range</text>',
    ]
    v = x0v
    while v <= x1v + 1e-9:
        x = X(v)
        o.append(f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{top + rh * len(rows)}" stroke="{GRID}" stroke-width="1"/>')
        o.append(f'<text x="{x:.1f}" y="{top + rh * len(rows) + 18}" font-size="11" text-anchor="middle" fill="{MUTED}">{"0" if abs(v) < 1e-9 else format(v, "+g")}</text>')
        v += step
    o.append(f'<text x="{left + plot_w / 2:.1f}" y="{H - 10}" font-size="12" text-anchor="middle" fill="{INK}">difference in 180-day completion, percentage points (orchestrated minus baseline)</text>')
    for i, r in enumerate(rows):
        y = top + i * rh
        if i % 2 == 0:
            o.append(f'<rect x="0" y="{y}" width="{W}" height="{rh}" fill="#f5f7fa"/>')
        o.append(f'<text x="{left - 10}" y="{y + 17}" font-size="12" text-anchor="end" fill="{INK}">{escape(r["key"])}</text>')
        for end, col in (("low", BLUE), ("high", ORANGE)):
            d = 100 * r[end]["metrics"]["completion_180d"]["difference"]
            xa, xb = sorted((X(base), X(d)))
            o.append(f'<rect x="{xa:.1f}" y="{y + 5}" width="{max(xb - xa, 0.8):.1f}" height="{rh - 10}" fill="{col}"/>')
            right_side = d >= base
            tx = xb + 4 if right_side else xa - 4
            o.append(f'<text x="{tx:.1f}" y="{y + 17}" font-size="10.5" text-anchor="{"start" if right_side else "end"}" fill="{INK}">{escape(_v(r[end]["value"]))}</text>')
    o.append(f'<line x1="{X(base):.1f}" y1="{top - 6}" x2="{X(base):.1f}" y2="{top + rh * len(rows)}" stroke="{INK}" stroke-width="1.5" stroke-dasharray="5 4"/>')
    o.append(f'<text x="{X(base):.1f}" y="{top - 10}" font-size="11" text-anchor="middle" fill="{INK}">base {base:+.1f}</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"
