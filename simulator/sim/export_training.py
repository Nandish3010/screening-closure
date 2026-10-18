"""Training data and the risk model for the worklist score (D8, D10).

Runs the orchestrated arm of the literature-anchored scenario for N cases, cuts each case's event log at
one random snapshot day, and turns what a worklist could have known at that moment into a feature
row with the label "no colonoscopy within 180 days of the result" (lost to follow-up). It then fits
the logistic regression of `api/app/risk`, validates it on a held-out split and writes

    api/app/risk/model.json                coefficients, feature spec, training metadata
    docs/results/risk/risk.json            every number of the report (metrics, calibration, groups)
    docs/results/risk/training.md          the report
    docs/results/risk/calibration.svg      reliability diagram
    docs/governance/risk-score-card.md     the generated blocks of the card (between risk:NAME markers)

Everything derives from the seed and the parameter files: no wall clock, no paths, no timings, so
two runs write identical bytes (T-D11, T-D12). `--check` regenerates in memory and fails if a
committed file differs.

Features (see `app.risk.features`): preferred channel, age band, contact flag, pathway stage,
days since the result, prior escalations, prior handoff bounces, consent withdrawal, provider change.
`language` is deliberately NOT a feature (D12: a proxy for nationality). Each row still carries the
patient's language, but only as an audit label: the fairness check reports AUC, ECE and the gap between
mean predicted probability and observed rate per language group, to show the score does not encode language
indirectly. The simulator models no age, so the age band is drawn by this exporter from its own seeded stream,
independent of every behaviour: it is a null feature by construction (its coefficient should be near
zero), kept so the score card can show the pipeline does not invent signal.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from . import DEFAULT_CONSERVATIVE, DEFAULT_PARAMS, REPO_ROOT
from .rng import derive_seed, stream
from .runner import Context, load_context, make_patients, run_arm

from app.risk import features as F  # noqa: E402  (sim/__init__ puts the repo's api/ on sys.path)
from app.risk import report as R
from app.risk import train as T
from app.risk.model import Model

DEFAULT_SEED = 20261017
DEFAULT_CASES = 20000
FOLLOW_UP_DAYS = 180
SNAPSHOT_MAX_DAYS = 90.0
TRAIN_FRACTION = 0.7
L2 = 1.0
ECE_MAX = R.ECE_TOLERANCE

MODEL_JSON = REPO_ROOT / "api" / "app" / "risk" / "model.json"
RESULTS_DIR = REPO_ROOT / "docs" / "results" / "risk"
CARD = REPO_ROOT / "docs" / "governance" / "risk-score-card.md"
SCENARIO = "literature_anchored"

_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class ExportRow:
    key: str
    split: str          # train | heldout
    snapshot_day: float
    features: F.CaseFeatures
    label: int          # 1 = no colonoscopy within FOLLOW_UP_DAYS
    language: str       # audit label for the fairness check only; never a model input (D12)


def _age_band(seed: int, key: str) -> str:
    age = 40 + int(stream(seed, key, "risk-age").random() * 36)  # uniform 40..75, the screening range
    return F.age_band_of(age)


def _split(seed: int, key: str) -> str:
    return "train" if (derive_seed(seed, key, "risk-split") % 10_000) / 10_000 < TRAIN_FRACTION else "heldout"


def load_scenario_context() -> Context:
    return load_context([DEFAULT_PARAMS, DEFAULT_CONSERVATIVE], warn=False)


def build_rows(ctx: Context, cases: int, seed: int) -> list[ExportRow]:
    """One feature row per simulated case that is still at risk at its snapshot day."""
    patients = make_patients(ctx, cases, seed)
    result = run_arm(ctx, "orchestrated", cases, FOLLOW_UP_DAYS, seed, patients)
    main_to = {t.event: t.to_state for t in ctx.pathway.main_transitions}
    rows: list[ExportRow] = []
    for case in result.cases:
        p, log = case.p, case.log
        t0 = log[0].at
        rel = [(e.at - t0) / _DAY for e in log]
        t_complete = next((r for e, r in zip(log, rel) if e.type == "complete_confirmatory"), None)
        snapshot = round(stream(seed, p.key, "risk-snapshot").random() * SNAPSHOT_MAX_DAYS, 2)
        if t_complete is not None and t_complete <= snapshot:
            continue  # the colonoscopy is already done: nothing left to lose at this moment
        seen = [e for e, r in zip(log, rel) if r <= snapshot]
        state = ctx.pathway.initial_state
        for e in seen:
            state = main_to.get(e.type, state)
        stage = F.stage_of(state)
        if stage is None:  # cannot happen: past the colonoscopy means complete_confirmatory is in `seen`
            raise AssertionError(f"case {p.key} is at {state!r} but has not completed the confirmatory step")
        s = F.summarise_events(seen)
        rows.append(ExportRow(
            key=p.key,
            split=_split(seed, p.key),
            snapshot_day=snapshot,
            features=F.CaseFeatures(
                stage=stage,
                days_since_result=F.cap_days(snapshot),
                prior_escalations=s.prior_escalations,
                prior_bounces=s.prior_bounces,
                consent_withdrawn=s.consent_withdrawn,
                provider_changed=s.provider_changed,
                contact_valid=p.contact_valid,
                channel=p.channel,
                age_band=_age_band(seed, p.key),
            ),
            label=int(t_complete is None),
            language=p.language,
        ))
    return rows


def _row_fields(r: ExportRow) -> list:
    f = r.features
    return [r.key, r.split, f"{r.snapshot_day:.2f}", r.language, f.channel, f.age_band, int(f.contact_valid),
            f.stage, f"{f.days_since_result:.2f}", f.prior_escalations, f.prior_bounces,
            int(f.consent_withdrawn), int(f.provider_changed), r.label]


CSV_HEADER = ["case_key", "split", "snapshot_day", "language_audit_only", "channel", "age_band", "contact_valid", "stage",
              "days_since_result", "prior_escalations", "prior_bounces", "consent_withdrawn", "provider_changed",
              "lost_to_follow_up_180d"]


def rows_sha256(rows: list[ExportRow]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(("|".join(map(str, _row_fields(r))) + "\n").encode())
    return h.hexdigest()


def write_rows_csv(rows: list[ExportRow], path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        w.writerows(_row_fields(r) for r in rows)


def _gap(groups: list[dict], key: str) -> float | None:
    vals = [g[key] for g in groups if g["n"] > 0 and g[key] is not None]
    return T.r8(max(vals) - min(vals)) if len(vals) >= 2 else None


def train_and_report(ctx: Context, cases: int, seed: int) -> dict:
    """Everything the documents need: the fitted model dict and the full report dict."""
    rows = build_rows(ctx, cases, seed)
    train = [(r.features, r.label) for r in rows if r.split == "train"]
    held = [(r.features, r.label) for r in rows if r.split == "heldout"]
    held_language = [r.language for r in rows if r.split == "heldout"]  # audit labels, never given to the model
    model_dict, info = T.fit(train, l2=L2)
    model = Model(model_dict)
    base_rate = sum(y for _f, y in train) / len(train)
    heldout = T.evaluate(model, held, base_rate)
    # How the score performs as the API serves it today: no registry gives channel or age band, so they are
    # unknown and imputed. Same held-out cases, same model.
    masked = [(replace(f, channel=None, age_band=None), y) for f, y in held]
    served = T.evaluate(model, masked, base_rate)
    noise = T.ece_noise_floor([model.predict(f) for f, _y in held], derive_seed(seed, "risk-noise"))
    groups = {}
    for feature, levels in (("language", F.LANGUAGES), ("channel", F.CHANNELS), ("age_band", F.AGE_BANDS)):
        labels = held_language if feature == "language" else [f.value_of(feature) for f, _y in held]
        gm = T.group_metrics(model, held, base_rate, feature, levels, labels)
        for g in gm:
            sub = [model.predict(f) for (f, _y), lab in zip(held, labels) if lab == g["group"]]
            g["ece_noise_p95"] = T.ece_noise_floor(sub, derive_seed(seed, "risk-noise", feature, g["group"]), 100)["p95"] if sub else None
        groups[feature] = gm
    mp = ctx.mp
    train_metrics = T.evaluate(model, train, base_rate)
    coef_hash = T.sha256_of({"intercept": model_dict["intercept"], "coef": [c["coef"] for c in model_dict["columns"]]})
    meta = {
        "scenario": SCENARIO,
        "arm": "orchestrated",
        "params_files": ["simulator/params.yaml", "simulator/params.conservative.yaml"],
        "params_hash": ctx.params.hash(),
        "seed": seed,
        "n_cases": cases,
        "n_rows": len(rows),
        "excluded_features": list(F.EXCLUDED_FEATURES),
        "n_train": len(train),
        "n_heldout": len(held),
        "train_fraction": TRAIN_FRACTION,
        "prevalence_train": T.r8(base_rate),
        "snapshot_max_days": SNAPSHOT_MAX_DAYS,
        "follow_up_days": FOLLOW_UP_DAYS,
        "simulated_start_date": mp.start_date,
        "intake_window_days": mp.intake_window_days,
        "optimizer": "newton_raphson_l2",
        "l2": L2,
        "iterations": info["iterations"],
        "train_log_loss": info["train_log_loss"],
        "data_sha256": rows_sha256(rows),
        "coefficient_sha256": coef_hash,
    }
    named = {**model_dict, "columns": [{"name": F.column_name(c["feature"], c["kind"], c["level"]), **c}
                                       for c in model_dict["columns"]]}
    risk = {
        "meta": meta,
        "model": named,
        "heldout": heldout,
        "train": {k: train_metrics[k] for k in ("n", "prevalence", "auc", "brier", "ece")},
        "heldout_attributes_unknown": {k: served[k] for k in ("n", "auc", "brier", "ece")},
        "noise_floor": noise,
        "tolerance": {"ece_max": ECE_MAX, "met": heldout["ece"] <= ECE_MAX,
                      "brier_beats_base_rate": heldout["brier"] < heldout["brier_base_rate"]},
        "groups": groups,
        "group_gaps": {f: {"auc": _gap(gs, "auc"), "ece": _gap(gs, "ece")} for f, gs in groups.items()},
        # Calibration in the large per group: mean predicted probability against observed rate (D12).
        "calibration_gap": {
            "tolerance": T.GAP_TOLERANCE,
            **{f: {"max_abs_gap": T.r8(max(abs(g["gap"]) for g in gs if g["gap"] is not None)),
                   "flagged": [g["group"] for g in gs if g["gap_flag"]]}
               for f, gs in groups.items()},
        },
    }
    model_file = {
        "model_type": "logistic_regression",
        "schema_version": 1,
        "label": f"no colonoscopy within {FOLLOW_UP_DAYS} days of the result (lost to follow-up)",
        "intended_use": "worklist ordering only; see docs/governance/risk-score-card.md",
        "synthetic_data": True,
        "excluded_features": {"language": "a proxy for nationality: it must not rank patients (D12); audited as an outcome group only"},
        **model_dict,
        "training": meta,
        "validation": {"n": heldout["n"], "auc": heldout["auc"], "brier": heldout["brier"],
                       "brier_base_rate": heldout["brier_base_rate"], "ece": heldout["ece"],
                       "ece_tolerance": ECE_MAX},
    }
    return {"model_file": model_file, "risk": risk, "rows": rows}


def render_files(result: dict, card_text: str | None) -> dict[Path, str]:
    """path -> text for every generated file. `card_text` is the current card (its generated blocks are
    replaced); None skips the card."""
    risk = result["risk"]
    files = {
        MODEL_JSON: T.canonical_json(result["model_file"]),
        RESULTS_DIR / "risk.json": T.canonical_json(risk),
        RESULTS_DIR / "training.md": R.training_md(risk),
        RESULTS_DIR / "calibration.svg": R.reliability_svg(risk["heldout"]),
    }
    if card_text is not None:
        files[CARD] = R.splice(card_text, R.card_blocks(risk))
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m simulator.sim.export_training", description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", type=int, default=DEFAULT_CASES)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--check", action="store_true", help="regenerate in memory; exit 1 if any committed file differs")
    ap.add_argument("--rows-csv", default=None, help="also write the feature rows to this CSV (not committed)")
    args = ap.parse_args(argv)

    result = train_and_report(load_scenario_context(), args.cases, args.seed)
    if args.rows_csv:
        write_rows_csv(result["rows"], Path(args.rows_csv))
    files = render_files(result, CARD.read_text() if CARD.exists() else None)
    risk = result["risk"]
    h = risk["heldout"]
    summary = (f"risk score: {risk['meta']['n_rows']} rows, held-out n={h['n']}, AUC {h['auc']:.4f}, Brier {h['brier']:.4f} "
               f"(base rate {h['brier_base_rate']:.4f}), ECE {h['ece']:.4f} (tolerance {ECE_MAX}: "
               f"{'met' if risk['tolerance']['met'] else 'NOT met'})")
    if args.check:
        stale = [str(p.relative_to(REPO_ROOT)) for p, text in files.items() if not p.exists() or p.read_text() != text]
        print(summary)
        if stale:
            print("risk-report --check: FAIL, these files differ from a fresh run: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("risk-report --check: PASS, committed files are byte-identical to a fresh run")
        return 0
    for p, text in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    print(summary)
    print("wrote " + ", ".join(str(p.relative_to(REPO_ROOT)) for p in files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
