"""Markdown and SVG for the risk score's documents (D8, D10). Pure functions of the `risk.json`
dict that `simulator/sim/export_training.py` builds, so the documents regenerate byte-identically
from the seed (T-D12). No plotting library: the reliability diagram is written as SVG by hand.
"""

from __future__ import annotations

import math
import re

ECE_TOLERANCE = 0.05
SMALL_GROUP = 300  # below this many held-out rows a group's ECE is dominated by chance


def _f(x, nd=4) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _pct(x, nd=1) -> str:
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


# ---------------------------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------------------------

def reliability_svg(heldout: dict) -> str:
    """Reliability diagram: observed rate against mean predicted probability per non-empty bin
    (marker area follows the bin's count), the diagonal of perfect calibration, and the count of
    held-out cases per probability bin underneath."""
    bins = heldout["calibration"]
    x0, y0, size = 64.0, 56.0, 400.0   # plot square: left, top, side
    hist_top, hist_h = y0 + size + 58.0, 60.0
    width, height = 520, int(hist_top + hist_h + 34)
    max_n = max(b["n"] for b in bins) or 1

    def px(v: float) -> str:
        return f"{x0 + v * size:.2f}"

    def py(v: float) -> str:
        return f"{y0 + (1.0 - v) * size:.2f}"

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'role="img" aria-labelledby="t d" font-family="Helvetica, Arial, sans-serif" font-size="12">',
        "<title id=\"t\">Reliability diagram of the loss-to-follow-up risk score on the held-out simulator data</title>",
        f'<desc id="d">Observed rate of loss to follow-up against mean predicted probability in ten equal-width bins; '
        f'expected calibration error {_f(heldout["ece"], 3)}, AUC {_f(heldout["auc"], 3)}, {heldout["n"]} held-out cases. '
        'Synthetic data.</desc>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{x0:.0f}" y="24" font-size="15" font-weight="bold" fill="#222222">Risk score reliability, held-out simulator data</text>',
        f'<text x="{x0:.0f}" y="42" fill="#555555">n = {heldout["n"]}, ECE = {_f(heldout["ece"], 3)}, '
        f'AUC = {_f(heldout["auc"], 3)}, Brier = {_f(heldout["brier"], 3)} (base-rate predictor {_f(heldout["brier_base_rate"], 3)})</text>',
    ]
    for i in range(11):
        v = i / 10
        out.append(f'<line x1="{px(v)}" y1="{py(0)}" x2="{px(v)}" y2="{py(1)}" stroke="#e3e3e3" stroke-width="1"/>')
        out.append(f'<line x1="{px(0)}" y1="{py(v)}" x2="{px(1)}" y2="{py(v)}" stroke="#e3e3e3" stroke-width="1"/>')
        if i % 2 == 0:
            out.append(f'<text x="{px(v)}" y="{y0 + size + 16:.2f}" text-anchor="middle" fill="#444444">{v:.1f}</text>')
            out.append(f'<text x="{x0 - 8:.2f}" y="{float(py(v)) + 4:.2f}" text-anchor="end" fill="#444444">{v:.1f}</text>')
    out.append(f'<rect x="{x0:.0f}" y="{y0:.0f}" width="{size:.0f}" height="{size:.0f}" fill="none" stroke="#888888"/>')
    out.append(f'<line x1="{px(0)}" y1="{py(0)}" x2="{px(1)}" y2="{py(1)}" stroke="#888888" stroke-dasharray="5 4"/>')
    pts = [(b["mean_predicted"], b["observed_rate"], b["n"]) for b in bins if b["n"]]
    if len(pts) > 1:
        path = " ".join(f"{px(p)},{py(o)}" for p, o, _n in pts)
        out.append(f'<polyline points="{path}" fill="none" stroke="#1f6feb" stroke-width="1.5"/>')
    for p, o, n in pts:
        r = 3.0 + 8.0 * math.sqrt(n / max_n)
        out.append(f'<circle cx="{px(p)}" cy="{py(o)}" r="{r:.2f}" fill="#1f6feb" fill-opacity="0.75" stroke="#ffffff"/>')
    out.append(f'<text x="{x0 + size / 2:.2f}" y="{y0 + size + 36:.2f}" text-anchor="middle" fill="#222222">Mean predicted probability (bin)</text>')
    out.append(f'<text x="18" y="{y0 + size / 2:.2f}" text-anchor="middle" fill="#222222" '
               f'transform="rotate(-90 18 {y0 + size / 2:.2f})">Observed rate of loss to follow-up</text>')
    # histogram of held-out cases per predicted-probability bin
    out.append(f'<text x="{x0:.0f}" y="{hist_top - 6:.2f}" fill="#444444">Held-out cases per probability bin</text>')
    bw = size / len(bins)
    for i, b in enumerate(bins):
        h = hist_h * b["n"] / max_n
        out.append(f'<rect x="{x0 + i * bw + 2:.2f}" y="{hist_top + hist_h - h:.2f}" width="{bw - 4:.2f}" height="{h:.2f}" fill="#9db7e5"/>')
        out.append(f'<text x="{x0 + i * bw + bw / 2:.2f}" y="{hist_top + hist_h + 14:.2f}" text-anchor="middle" fill="#444444" font-size="10">{b["n"]}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------------------------

def calibration_table(heldout: dict) -> str:
    rows = ["| Bin | Predicted range | Cases | Mean predicted | Observed rate | Absolute gap |", "|---|---|---|---|---|---|"]
    for b in heldout["calibration"]:
        rows.append(
            f"| {b['bin'] + 1} | {b['low']:.1f} to {b['high']:.1f} | {b['n']} | {_f(b['mean_predicted'])} | "
            f"{_f(b['observed_rate'])} | {_f(b['abs_gap'])} |"
        )
    return "\n".join(rows)


def headline_table(risk: dict) -> str:
    h, tol, nf = risk["heldout"], risk["tolerance"], risk["noise_floor"]
    verdict = "met" if tol["met"] else "not met"
    u = risk["heldout_attributes_unknown"]
    return "\n".join([
        "| Measure (held-out split) | Value |",
        "|---|---|",
        f"| Held-out cases | {h['n']} (of {risk['meta']['n_rows']} rows from {risk['meta']['n_cases']} simulated cases) |",
        f"| Prevalence of loss to follow-up | {_pct(h['prevalence'])} (training split {_pct(risk['meta']['prevalence_train'])}) |",
        f"| AUC | {_f(h['auc'])} |",
        f"| Brier score | {_f(h['brier'])} |",
        f"| Brier score of the base-rate predictor | {_f(h['brier_base_rate'])} |",
        f"| Brier skill score (1 minus the ratio) | {_f(h['brier_skill'])} |",
        f"| Expected calibration error (ECE, 10 equal-width bins) | {_f(h['ece'])} |",
        f"| ECE of a perfectly calibrated model at this sample size (mean, 95th percentile) | {_f(nf['mean'])}, {_f(nf['p95'])} |",
        f"| Tolerance: ECE at or below {tol['ece_max']} | {verdict} |",
        f"| As served today, with channel and age band unknown (AUC, Brier, ECE) | {_f(u['auc'])}, {_f(u['brier'])}, {_f(u['ece'])} |",
    ]) + "\n\n" + verdict_sentence(risk)


def verdict_sentence(risk: dict) -> str:
    tol, h = risk["tolerance"], risk["heldout"]
    if tol["met"]:
        return (f"The calibration tolerance is met: ECE {_f(h['ece'])} is at or below {tol['ece_max']}, and the Brier score is "
                f"{'better' if tol['brier_beats_base_rate'] else 'NOT better'} than the base-rate predictor.")
    return (f"The calibration tolerance is NOT met: ECE {_f(h['ece'])} is above {tol['ece_max']}. The score is kept as a worklist "
            "ordering aid (its ranking is still informative), but its probabilities must not be read as calibrated risks.")


def _signed(x, nd=4) -> str:
    return "n/a" if x is None else f"{x:+.{nd}f}"


def fairness_table(risk: dict) -> str:
    rows = [
        "| Group | Value | Held-out cases | Prevalence | AUC | ECE | Chance ECE (95th percentile) | Brier "
        "| Mean predicted | Observed rate | Gap (predicted minus observed) | Sampling margin (95%) | Gap flag |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    tol = risk["calibration_gap"]["tolerance"]
    for feature, label in (("language", "Language (audit only, not an input)"), ("channel", "Preferred channel"),
                           ("age_band", "Age band")):
        for g in risk["groups"][feature]:
            small = " (small sample)" if g["n"] < SMALL_GROUP else ""
            flag = "n/a" if g["gap"] is None else ("FLAG" if g["gap_flag"] else "ok")
            rows.append(
                f"| {label} | {g['group']} | {g['n']}{small} | {_pct(g['prevalence'])} | {_f(g['auc'])} | "
                f"{_f(g['ece'])} | {_f(g.get('ece_noise_p95'))} | {_f(g['brier'])} | {_f(g['mean_predicted'])} | "
                f"{_f(g['prevalence'])} | {_signed(g['gap'])} | {_f(g['gap_margin_95'])} | {flag} |"
            )
    gaps, cg = risk["group_gaps"], risk["calibration_gap"]
    rows.append("")
    rows.append(
        "Largest gap between groups (maximum minus minimum): "
        + "; ".join(
            f"{label} AUC {_f(gaps[f]['auc'], 3)}, ECE {_f(gaps[f]['ece'], 3)}"
            for f, label in (("language", "language"), ("channel", "channel"), ("age_band", "age band"))
        )
        + "."
    )
    rows.append("")
    rows.append(f"Mean predicted probability against observed rate, flag threshold {tol}: "
                + "; ".join(gap_phrase(label, cg[f], risk["groups"][f])
                            for f, label in (("language", "language"), ("channel", "channel"), ("age_band", "age band")))
                + ".")
    return "\n".join(rows)


def gap_phrase(label: str, summary: dict, groups: list[dict]) -> str:
    if not summary["flagged"]:
        return f"{label}: no group flagged (largest gap {_f(summary['max_abs_gap'], 4)})"
    shown = ", ".join(f"{g['group']} {_signed(g['gap'], 3)}" for g in groups if g["gap_flag"])
    return f"{label}: FLAGGED, {shown} (largest gap {_f(summary['max_abs_gap'], 4)})"


def coefficient_table(risk: dict) -> str:
    rows = ["| Column | Coefficient (log-odds) | Odds ratio | Unit |", "|---|---|---|---|",
            f"| intercept | {risk['model']['intercept']:.4f} | n/a | baseline logit for an all-zero row |"]
    for c in risk["model"]["columns"]:
        name = c["name"]
        unit = {"onehot": "1 if true, else 0", "bool": "1 if true, else 0"}.get(
            c["kind"], f"per 1 SD ({c['scale']:.2f}; mean {c['center']:.2f})")
        rows.append(f"| {name} | {c['coef']:.4f} | {math.exp(c['coef']):.3f} | {unit} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------------------------

def training_md(risk: dict) -> str:
    m = risk["meta"]
    return f"""# Risk score training report

Generated by `python -m simulator.sim.export_training` (do not edit by hand; the file is rewritten byte-for-byte from the seed). Model: [`api/app/risk/model.json`](../../../api/app/risk/model.json). Card with intended use and limits: [`docs/governance/risk-score-card.md`](../../governance/risk-score-card.md).

All data are synthetic. The score is a demonstration of the pattern, not a validated model.

## Training data

| Item | Value |
|---|---|
| Scenario | {m['scenario']}, orchestrated arm only |
| Parameter files | {', '.join(f'`{p}`' for p in m['params_files'])} |
| Parameter hash | `{m['params_hash']}` |
| Simulator seed | {m['seed']} |
| Simulated start date | {m['simulated_start_date']} (results arrive over {m['intake_window_days']:g} days, each case followed for {m['follow_up_days']} days) |
| Simulated cases | {m['n_cases']} |
| Rows | {m['n_rows']} (one snapshot per case, taken at a random day in the first {m['snapshot_max_days']:g} days after the result; cases already past the colonoscopy at the snapshot are not at risk and are left out) |
| Training split | {m['n_train']} rows ({_pct(m['train_fraction'], 0)}, assigned by a hash of the case key) |
| Held-out split | {m['n_heldout']} rows |
| Label | no colonoscopy within {m['follow_up_days']} days of the result (lost to follow-up) |
| Prevalence | {_pct(m['prevalence_train'])} in the training split |
| Fit | L2-regularised logistic regression, lambda {m['l2']:g}, Newton-Raphson from zero, {m['iterations']} iterations, standard library only |
| Row hash | `{m['data_sha256']}` |
| Coefficient hash | `{m['coefficient_sha256']}` |

## Performance on the held-out split

{headline_table(risk)}

The tolerance was proposed at 0.05 (ticket D10).

## Calibration table

{calibration_table(risk['heldout'])}

![Reliability diagram](calibration.svg)

## Fairness by group (held-out split)

`language` is not a model input (D12). It is reported here, with channel and age band, only as an outcome audit: for every group, AUC, ECE and the gap between the mean predicted probability and the observed rate (flag when the gap exceeds {risk['calibration_gap']['tolerance']}).

{fairness_table(risk)}

## Coefficients

Numeric columns are standardised on the training split, so their coefficients are per standard deviation. A feature that is unknown for a case is encoded at its training mean and adds nothing relative to the average case.

{coefficient_table(risk)}

## Reproduce

```
PYTHONPATH=api:. .venv/bin/python -m simulator.sim.export_training --cases {m['n_cases']} --seed {m['seed']}
PYTHONPATH=api:. .venv/bin/python -m simulator.sim.export_training --check   # fails if the committed files differ
```
"""


BLOCK = re.compile(r"(<!-- risk:(\w+):begin -->\n)(.*?)(\n<!-- risk:\2:end -->)", re.S)


def card_blocks(risk: dict) -> dict[str, str]:
    return {
        "performance": headline_table(risk),
        "calibration": calibration_table(risk["heldout"]) + "\n\n![Reliability diagram](../results/risk/calibration.svg)",
        "fairness": fairness_table(risk),
        "coefficients": coefficient_table(risk),
    }


def splice(card: str, blocks: dict[str, str]) -> str:
    """Replace the text between `<!-- risk:NAME:begin -->` and `<!-- risk:NAME:end -->` markers."""
    seen = set()

    def repl(m: re.Match) -> str:
        name = m.group(2)
        if name not in blocks:
            raise KeyError(f"risk-score-card.md has a block {name!r} the report does not produce")
        seen.add(name)
        return m.group(1) + blocks[name] + m.group(4)

    out = BLOCK.sub(repl, card)
    missing = set(blocks) - seen
    if missing:
        raise KeyError(f"risk-score-card.md lacks the generated block(s) {sorted(missing)}")
    return out
