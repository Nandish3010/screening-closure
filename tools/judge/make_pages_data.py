#!/usr/bin/env python3
"""Generate the data behind the three judge pages (AI and safety, Integrations, Pilot value) from committed files.

Writes two files, deterministically (sorted keys, indent 2, trailing newline, no timestamps, standard library only):

    web/src/judge/safetyData.json   reply-triage evaluation: docs/results/triage/eval.json, eval-gguf-q8_0.json,
                                    tools/triage/dataset/manifest.json and docs/results/fairness.json
    web/src/judge/pilotValue.json   the pilot value calculator's inputs and reference outputs: tools/pilot/inputs.yaml,
                                    docs/results/results.json (effort), docs/results/abudhabi.json (completion), and
                                    reference outputs computed by tools/pilot/cost_model.py itself

    python tools/judge/make_pages_data.py            write both files
    python tools/judge/make_pages_data.py --check    exit 1 if a committed file differs

No number on those pages is typed by hand: the web code formats what is in these files. The calculator's formula is
ported to TypeScript (web/src/lib/pilotValue.ts); the reference block here is what the Python model returns for fixed
inputs, and web/tests/pilot_value.test.ts asserts that the port matches it. Everything is synthetic or assumed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "web" / "src" / "judge"
SAFETY_OUT = OUT_DIR / "safetyData.json"
PILOT_OUT = OUT_DIR / "pilotValue.json"

TRIAGE = ROOT / "docs" / "results" / "triage"
EVAL_MLX = TRIAGE / "eval.json"
EVAL_GGUF = TRIAGE / "eval-gguf-q8_0.json"
MANIFEST = ROOT / "tools" / "triage" / "dataset" / "manifest.json"
FAIRNESS = ROOT / "docs" / "results" / "fairness.json"
RESULTS = ROOT / "docs" / "results" / "results.json"
ABUDHABI = ROOT / "docs" / "results" / "abudhabi.json"
INPUTS = ROOT / "tools" / "pilot" / "inputs.yaml"

SCENARIO = "literature_anchored"
ROLES = ("coordinator", "owner_clinician", "specialist")
LANGUAGES = ("en", "ar", "arabizi", "mixed")
DIALECTS = ("ar_msa", "ar_gulf")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _cost_model():
    spec = importlib.util.spec_from_file_location("cost_model_for_pages", ROOT / "tools" / "pilot" / "cost_model.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- safety page ------------------------------------------------------------------------------------------------


def _ci(pair) -> list[float] | None:
    return None if pair is None else [pair[0], pair[1]]


def _group(g: dict) -> dict:
    """One group's row through the deployed path (rule on): the fields the page shows."""
    return {
        "n": g["n"], "accuracy": g["accuracy"], "coverage": g["coverage"], "auto_handled_n": g["auto_handled_n"],
        "auto_precision": g["auto_precision"], "auto_precision_ci": _ci(g["auto_precision_ci"]),
        "wrong_automatic_steps": g["wrong_automatic_steps"],
    }


def _plain_language(per_language: dict) -> dict:
    return {k: {"n": v["n"], "accuracy": v["accuracy"], "auto_n": v["auto_n"], "auto_precision": v["auto_precision"],
                "auto_precision_ci": _ci(v["auto_precision_ci"]), "macro_f1": v["macro_f1"]}
            for k, v in per_language.items() if k in LANGUAGES}


def _model(ev: dict) -> dict:
    return {
        "accuracy": ev["accuracy"], "macro_f1": ev["macro_f1"], "auto_precision": ev["auto_precision"],
        "auto_precision_ci": _ci(ev["auto_precision_ci"]), "coverage": ev["coverage"],
        "ece_calibrated": ev["ece_calibrated"], "temperature": ev["temperature"], "threshold": ev["threshold"],
        "opt_out_leaked": ev["opt_out_leaked"], "opt_out_total": ev["opt_out_total"],
        "per_language_no_rule": _plain_language(ev["per_language"]),
    }


def build_safety() -> dict:
    mlx, gg = _load(EVAL_MLX), _load(EVAL_GGUF)
    man, fair = _load(MANIFEST), _load(FAIRNESS)
    rule = gg["groups"]["with_rule"]
    by_lang, by_dialect = rule["by_language"], rule["by_dialect"]
    ft = fair["triage"]
    return {
        "synthetic": True,
        "dataset": {
            "seed": man["seed"], "total": man["total"], "per_intent": man["per_intent"],
            "test_by_label": man["counts"]["test"]["by_label"], "test_by_language": man["counts"]["test"]["by_language"],
            "test_templates": man["counts"]["test"]["templates"], "n": mlx["n"],
        },
        "baseline": mlx["baseline"],
        "original": dict(_model(mlx), label="Original (MLX, 4-bit base with LoRA adapter)"),
        "hosted": dict(
            _model(gg), label="Hosted build (GGUF, Q8_0)", model_id=gg["model_id"], quantization=gg["quantization"],
            by_language_with_rule={k: _group(by_lang[k]) for k in ("all",) + LANGUAGES},
            by_dialect_with_rule={k: dict(_group(by_dialect[k])) for k in DIALECTS},
            arabizi_threshold=gg["groups"]["arabizi_threshold_in_force"],
            mlx_arabizi_threshold=gg["groups"]["mlx_arabizi_threshold"],
        ),
        "fairness": {
            "thresholds": fair["thresholds"],
            "triage_flags": [f for f in fair["flags"] if f["area"] == "triage"],
            "original_with_rule_all": _group(ft["by_language"]["all"]),
        },
    }


# --- pilot value page -------------------------------------------------------------------------------------------

SHADOW_KEYS = (
    "positivity_rate", "pilot_providers", "pilot_fit_screened_per_provider_per_year", "shadow_days",
    "hosting_fixed_aed_per_month", "hosting_variable_aed_per_1000_cases_per_year", "third_party_licences_aed_per_year",
    "support_fte_loaded_cost_aed_per_year", "shadow_integration_one_off_aed_per_provider", "shadow_support_fte",
    "shadow_governance_review_one_off_aed", "shadow_hosting_share_of_full", "shadow_licences_share_of_full",
)
DISPLAY_ONLY_KEYS = (
    "abu_dhabi_population_40_to_75", "fit_participation_annual", "fit_positivity_market", "coordinator_cost_aed_per_hour",
    "programme_licence_aed_per_year", "licence_pathways",
)


def shadow_cost(cm, v: dict, cases: float) -> dict:
    """The real `cost_model.shadow_pilot`, run for a given number of positive cases: the FIT volume per provider is set
    so that providers x volume x days / 365 x positivity equals `cases`. Nothing in the model is changed."""
    w = dict(v)
    w["pilot_fit_screened_per_provider_per_year"] = cases * 365 / (v["pilot_providers"] * v["shadow_days"] * v["positivity_rate"])
    r = cm.shadow_pilot(w)
    return {k: r[k] for k in ("fits", "cases", "days", "hosting", "licences", "support", "integration", "governance", "total",
                              "cost_per_case")}


def build_pilot() -> dict:
    cm = _cost_model()
    _, spec = cm.load_inputs(INPUTS)
    v = {k: s["value"] for k, s in spec.items()}
    res, ad = _load(RESULTS), _load(ABUDHABI)
    eff = res["effort"]["scenarios"][SCENARIO]
    default_cases = cm.shadow_pilot(v)["cases"]
    annual_positives = cm.market_positives(v["abu_dhabi_population_40_to_75"], v["fit_participation_annual"],
                                           v["fit_positivity_market"])
    ad_s = ad["scenarios"][SCENARIO]
    baseline_completion = ad_s["baseline"]["completion_360d"]["mean"]
    ratio = ad_s["ratio"]["completion_360d"]

    def minutes(subset: str, arm: str) -> dict:
        block = eff[subset]["arms"][arm]
        return {r: block[f"minutes_per_case.{r}"]["mean"] for r in ROLES}

    def reduction(subset: str) -> dict:
        red = eff[subset]["reduction"]
        out = {"labour_reduction": red["labour_reduction"]["mean"]}
        for r in ROLES:
            out[r] = red[f"labour_reduction.{r}"]["mean"]
        return out

    inputs = {}
    for k in SHADOW_KEYS + DISPLAY_ONLY_KEYS:
        s = spec[k]
        inputs[k] = {"value": s["value"], "unit": s["unit"], "status": s["status"], "basis": s["basis"]}

    cases_list = [default_cases, 60.0, 300.0]
    reference = [{"cases": c, "shadow": shadow_cost(cm, v, c)} for c in cases_list]
    return {
        "synthetic": True,
        "inputs": inputs,
        "defaults": {
            "annual_positives": annual_positives,
            "current_completion": baseline_completion,
            "staff_cost_per_hour": v["coordinator_cost_aed_per_hour"],
            "pilot_cases": default_cases,
        },
        "completion": {
            "scenario": ad_s["label"] if "label" in ad_s else SCENARIO,
            "baseline_360d": baseline_completion,
            "orchestrated_360d": ad_s["orchestrated"]["completion_360d"]["mean"],
            "ratio_360d": ratio,
            "published_target_360d": ad["target"]["completion_360d"],
            "published_followed_up": ad["target"]["followed_up"],
            "published_positives": ad["target"]["positives"],
        },
        "effort": {
            "scenario": SCENARIO,
            "roles": list(ROLES),
            "all": {"baseline": minutes("all", "baseline"), "engine": minutes("all", "orchestrated"), "reduction": reduction("all")},
            "in_reach": {"baseline": minutes("in_reach", "baseline"), "engine": minutes("in_reach", "orchestrated"),
                         "reduction": reduction("in_reach")},
            "target_reduction": 0.4,
        },
        "reference": reference,
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generate() -> dict[Path, str]:
    return {SAFETY_OUT: render(build_safety()), PILOT_OUT: render(build_pilot())}


def main(argv: list[str]) -> int:
    files = generate()
    if "--check" in argv:
        bad = [p for p, t in files.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        for p in bad:
            print(f"{p.relative_to(ROOT)} is out of date with its sources; run `make judge-pages-data` and commit it.", file=sys.stderr)
        if bad:
            return 1
        print("judge pages data: up to date")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p, t in files.items():
        p.write_text(t, encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
