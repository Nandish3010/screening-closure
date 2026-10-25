#!/usr/bin/env python3
"""Fairness audit of the three AI decisions, by language (ticket M7-5). `make rai-fairness` / `make rai-fairness-check`.

Computes, from artefacts already committed, and writes docs/results/fairness.json and fairness.md:

1. Reply triage (api/app/triage): precision and recall by language (ar, en, arabizi, mixed) and by Arabic dialect
   (ar_msa, ar_gulf) on the held-out TEST split (tools/triage/dataset/test.jsonl). The model is not run: its recorded
   logits for every test (and validation) reply are replayed (tools/rai/data/triage_scores_*.json, written by
   tools/triage/evaluate.py on the committed adapter) through the DEPLOYED decision path, `classify_local` with the
   opt-out gate first and `decide` with the committed calibration. Before anything is reported, the replay must
   reproduce docs/results/triage/eval.json (accuracy, auto-handled precision and coverage, per language); if it does
   not, the scores are not the ones behind the committed evaluation and the script stops.
2. Risk score (api/app/risk): AUC, ECE and calibration-in-the-large by language, channel and age band, read from
   docs/results/risk/risk.json (written by `make risk-report` from the simulator; language is an audit label there and
   never a model input). Not recomputed here: `make risk-check` already proves that file equals a fresh retrain.
3. Output validator (api/app/ai/validate.py): false-negative rate (an attack accepted) and false-positive rate (a
   benign message rejected) by language, on the red-team attacks of tests/redteam/test_rt_validator.py and on the
   benign controls (the RT-V-15 controls, the RT-V-11 at-limit texts, and every template rendered with a set of
   varied owner names and dates).

Pure Python, no network, no database, about a second. Deterministic: `--check` regenerates in memory and fails if the
committed files differ by one byte. The thresholds that raise a flag are the constants below; every flag in the report
carries the mitigation in place and, where one is proposed but not shipped, says so.

Usage: PYTHONPATH=api:. python tools/rai/fairness.py [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "tests" / "redteam"))  # the red-team attack lists are the validator's test set

from app.triage import classify as triage  # noqa: E402
from app.triage import optout  # noqa: E402
from app.triage.labels import ALWAYS_HUMAN, AUTO_HANDLED, INTENTS  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
DATASET = ROOT / "tools/triage/dataset"
TRIAGE_EVAL = ROOT / "docs/results/triage/eval.json"
RISK_JSON = ROOT / "docs/results/risk/risk.json"
OUT_JSON = ROOT / "docs/results/fairness.json"
OUT_MD = ROOT / "docs/results/fairness.md"

# ---- flag thresholds (a flag is a finding to act on, not a test failure; the check fails only on drift)
AUTO_PRECISION_TARGET = 0.95  # the triage card's target for auto-handled replies, per group as well as overall
MACRO_RECALL_GAP = 0.10  # a group's macro recall more than this below the best group's
RISK_AUC_GAP = 0.05  # a group's AUC more than this below the best group's
RISK_ECE_MAX = 0.05  # the risk card's tolerance, per group
VALIDATOR_FN_MAX = 0.05  # share of attacks accepted, per language
VALIDATOR_FP_MAX = 0.0  # a benign template rejected is a defect: none allowed
MIN_GROUP_N = 30  # below this a rate is reported but called "too few to judge"
VAL_PRECISION_TARGET = 0.97  # as in tools/triage/evaluate.py: fit on validation to land at or above 0.95 on test


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [r(max(0.0, centre - half)), r(min(1.0, centre + half))]


def r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


# =============================================================================================== 1. triage


class ReplayScorer:
    """The scorer interface of app.triage.classify, answering with the recorded logits for each known text."""

    def __init__(self, rows: list[dict], scores: list[dict]) -> None:
        if len(rows) != len(scores):
            raise SystemExit("fairness: score file and split differ in length")
        self.by_text: dict[str, dict] = {}
        for row, sc in zip(rows, scores):
            self.by_text.setdefault(row["text"][: 600], sc)

    def score(self, text: str) -> dict:
        return self.by_text[text]


def load_split(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATASET / f"{name}.jsonl").open(encoding="utf-8")]


def replay(rows: list[dict], scores: list[dict]) -> list[dict]:
    """Every reply through the deployed path (opt-out gate, then the model's decision with the committed calibration)."""
    triage.set_backend(ReplayScorer(rows, scores), triage.Calibration.load())
    try:
        return [triage.classify_local(row["text"]) for row in rows]
    finally:
        triage.reset()


def model_only(scores: list[dict], cal: triage.Calibration) -> list[tuple[str, float, bool]]:
    """The model's own answer without the opt-out gate: (intent, probability, auto-handled), as evaluate.py scores it."""
    out = []
    for sc in scores:
        probs = triage.softmax(sc["logits"], cal.temperature)
        best = max(range(len(INTENTS)), key=lambda i: probs[i])
        intent, p = INTENTS[best], probs[best]
        out.append((intent, p, intent in AUTO_HANDLED and p >= cal.threshold and sc["mass"] >= cal.mass_floor))
    return out


def verify_against_eval(rows: list[dict], preds: list[tuple[str, float, bool]]) -> dict:
    """The replayed scores must reproduce the committed evaluation, or nothing below is about the shipped model."""
    ev = json.loads(TRIAGE_EVAL.read_text(encoding="utf-8"))
    acc = sum(1 for row, (p, _, _) in zip(rows, preds) if p == row["label"]) / len(rows)
    auto = [(row, p) for row, (p, _, a) in zip(rows, preds) if a]
    auto_prec = sum(1 for row, p in auto if p == row["label"]) / len(auto)
    checks = {"accuracy": (acc, ev["accuracy"]), "auto_precision": (auto_prec, ev["auto_precision"]),
              "coverage": (len(auto) / len(rows), ev["coverage"])}
    for lang, want in ev["per_language"].items():
        idx = [i for i, row in enumerate(rows) if row["language"] == lang]
        checks[f"{lang}.accuracy"] = (sum(1 for i in idx if preds[i][0] == rows[i]["label"]) / len(idx), want["accuracy"])
        checks[f"{lang}.auto_n"] = (sum(1 for i in idx if preds[i][2]), want["auto_n"])
    bad = {k: v for k, v in checks.items() if abs(v[0] - v[1]) > 1e-9}
    if bad:
        raise SystemExit(f"fairness: the recorded triage scores do not reproduce {rel(TRIAGE_EVAL)}: {bad}")
    return {"reproduces": rel(TRIAGE_EVAL), "quantities_checked": len(checks)}


def group_metrics(rows: list[dict], results: list[dict]) -> dict:
    n = len(rows)
    labels = sorted({row["label"] for row in rows}, key=INTENTS.index)
    per_intent = {}
    for intent in labels:
        tp = sum(1 for row, res in zip(rows, results) if row["label"] == intent and res["intent"] == intent)
        fp = sum(1 for row, res in zip(rows, results) if row["label"] != intent and res["intent"] == intent)
        fn = sum(1 for row, res in zip(rows, results) if row["label"] == intent and res["intent"] != intent)
        per_intent[intent] = {"precision": r(tp / (tp + fp)) if tp + fp else None, "recall": r(tp / (tp + fn)), "support": tp + fn}
    precisions = [v["precision"] or 0.0 for v in per_intent.values()]
    recalls = [v["recall"] for v in per_intent.values()]
    auto = [(row, res) for row, res in zip(rows, results) if not res["needs_human"]]
    auto_ok = sum(1 for row, res in auto if row["label"] == res["intent"])
    human_due = [res for row, res in zip(rows, results) if row["label"] in ALWAYS_HUMAN]
    opt = [res for row, res in zip(rows, results) if row["label"] == "opt_out"]
    gate = sum(1 for res in opt if res["model_id"] == optout.GATE_MODEL_ID)
    return {
        "n": n,
        "accuracy": r(sum(1 for row, res in zip(rows, results) if row["label"] == res["intent"]) / n),
        "macro_precision": r(sum(precisions) / len(precisions)),
        "macro_recall": r(sum(recalls) / len(recalls)),
        "per_intent": per_intent,
        "auto_handled_n": len(auto),
        "coverage": r(len(auto) / n),
        "auto_precision": r(auto_ok / len(auto)) if auto else None,
        "auto_precision_ci": wilson(auto_ok, len(auto)),
        "wrong_automatic_steps": len(auto) - auto_ok,
        "always_human_to_person": r(sum(1 for res in human_due if res["needs_human"]) / len(human_due)) if human_due else None,
        "always_human_auto_handled": sorted(f"{row['label']}->{res['intent']}" for row, res in zip(rows, results)
                                            if row["label"] in ALWAYS_HUMAN and not res["needs_human"]),
        "opt_out_n": len(opt),
        "opt_out_to_person": sum(1 for res in opt if res["needs_human"]),
        "opt_out_caught_by_gate": gate,
    }


def triage_threshold_for(rows: list[dict], scores: list[dict], cal: triage.Calibration, target: float) -> float | None:
    """The lowest confidence threshold at which the auto-handled replies of `rows` reach `target` precision (>= 10 of them)."""
    preds = model_only(scores, triage.Calibration(cal.temperature, 0.0, cal.mass_floor, cal.model_id))
    for tau in [round(x / 1000, 3) for x in range(int(cal.threshold * 1000), 1000)]:
        auto = [(row, p) for row, (p, c, a) in zip(rows, preds) if a and c >= tau]
        if len(auto) < 10:
            return None
        if sum(1 for row, p in auto if p == row["label"]) / len(auto) >= target:
            return tau
    return None


def triage_section() -> dict:
    cal = triage.Calibration.load()
    test, val = load_split("test"), load_split("val")
    t_scores = json.loads((DATA / "triage_scores_test.json").read_text())
    v_scores = json.loads((DATA / "triage_scores_val.json").read_text())
    integrity = verify_against_eval(test, model_only(t_scores, cal))
    results = replay(test, t_scores)

    groups: dict[str, dict] = {"all": group_metrics(test, results)}
    for lang in ("ar", "en", "arabizi", "mixed"):
        idx = [i for i, row in enumerate(test) if row["language"] == lang]
        groups[lang] = group_metrics([test[i] for i in idx], [results[i] for i in idx])
    dialects: dict[str, dict] = {}
    for variant in ("ar_msa", "ar_gulf"):
        idx = [i for i, row in enumerate(test) if row["variant"] == variant]
        dialects[variant] = group_metrics([test[i] for i in idx], [results[i] for i in idx])

    flags = []
    best_recall = max(g["macro_recall"] for k, g in groups.items() if k != "all")
    for name, g in [*((k, v) for k, v in groups.items() if k != "all"), *dialects.items()]:
        if g["auto_precision"] is not None and g["auto_precision"] < AUTO_PRECISION_TARGET:
            flags.append({"area": "triage", "group": name, "metric": "auto_precision", "value": g["auto_precision"],
                          "limit": AUTO_PRECISION_TARGET, "wrong_automatic_steps": g["wrong_automatic_steps"],
                          "of_auto_handled": g["auto_handled_n"]})
        if best_recall - g["macro_recall"] > MACRO_RECALL_GAP:
            flags.append({"area": "triage", "group": name, "metric": "macro_recall_gap", "value": r(best_recall - g["macro_recall"]),
                          "limit": MACRO_RECALL_GAP})
        if g["always_human_auto_handled"]:
            flags.append({"area": "triage", "group": name, "metric": "always_human_auto_handled", "value": len(g["always_human_auto_handled"]),
                          "limit": 0, "detail": g["always_human_auto_handled"]})
        if g["opt_out_n"] and g["opt_out_to_person"] < g["opt_out_n"]:
            flags.append({"area": "triage", "group": name, "metric": "opt_out_not_to_person",
                          "value": g["opt_out_n"] - g["opt_out_to_person"], "limit": 0})

    # Proposed mitigation (NOT shipped): a stricter threshold per flagged group, fitted on VALIDATION, measured on test.
    proposals = []
    for name in sorted({f["group"] for f in flags if f["metric"] == "auto_precision"}):
        key = "variant" if name.startswith("ar_") else "language"
        v_idx = [i for i, row in enumerate(val) if row[key] == name]
        t_idx = [i for i, row in enumerate(test) if row[key] == name]
        tau = triage_threshold_for([val[i] for i in v_idx], [v_scores[i] for i in v_idx], cal, VAL_PRECISION_TARGET)
        if tau is None:
            proposals.append({"group": name, "threshold": None, "shipped": False,
                              "note": "no threshold reaches the validation target with 10 or more auto-handled replies: route the group to a person"})
            continue
        if tau <= cal.threshold:
            proposals.append({"group": name, "threshold": None, "shipped": False, "validation_n": len(v_idx),
                              "note": (f"The deployed threshold already meets the {VAL_PRECISION_TARGET:.0%} target on this group's validation replies "
                                       f"({len(v_idx)}), so a stricter threshold fitted the same way changes nothing: the test shortfall is a "
                                       "generalisation gap between templates. No threshold fix is proposed; the mitigation is real replies "
                                       "of this group in the next training set (H2), and until then sending the group's replies to a person.")})
            continue
        stricter = triage.Calibration(cal.temperature, tau, cal.mass_floor, cal.model_id)
        g = group_metrics([test[i] for i in t_idx], replay_with([test[i] for i in t_idx], [t_scores[i] for i in t_idx], stricter))
        proposals.append({"group": name, "threshold_fitted_on": "validation", "validation_n": len(v_idx), "threshold": tau,
                          "deployed_threshold": cal.threshold, "test_auto_precision": g["auto_precision"],
                          "test_auto_precision_ci": g["auto_precision_ci"], "test_coverage": g["coverage"],
                          "test_auto_handled_n": g["auto_handled_n"], "shipped": False})

    return {
        "source": {"test_split": rel(DATASET / "test.jsonl"), "scores": rel(DATA / "triage_scores_test.json"),
                   "scores_sha256": sha256(DATA / "triage_scores_test.json"), "calibration": rel(triage.CALIBRATION_PATH),
                   "model_id": cal.model_id, "threshold": cal.threshold, "synthetic": True},
        "integrity": integrity,
        "by_language": groups,
        "by_dialect": dialects,
        "flags": flags,
        "proposed_mitigations": proposals,
    }


def replay_with(rows: list[dict], scores: list[dict], cal: triage.Calibration) -> list[dict]:
    triage.set_backend(ReplayScorer(rows, scores), cal)
    try:
        return [triage.classify_local(row["text"]) for row in rows]
    finally:
        triage.reset()


# =============================================================================================== 2. risk


def risk_section() -> dict:
    risk = json.loads(RISK_JSON.read_text(encoding="utf-8"))
    out: dict[str, list] = {}
    flags = []
    for feature in ("language", "channel", "age_band"):
        rows = risk["groups"][feature]
        best_auc = max(g["auc"] for g in rows)
        out[feature] = []
        for g in rows:
            entry = {k: g[k] for k in ("group", "n", "auc", "ece", "ece_noise_p95", "brier", "mean_predicted", "prevalence", "gap", "gap_margin_95", "gap_flag")}
            entry["auc_gap_to_best"] = r(best_auc - g["auc"])
            entry["ece_above_noise"] = g["ece"] > g["ece_noise_p95"]
            out[feature].append(entry)
            if g["gap_flag"]:
                flags.append({"area": "risk", "group": f"{feature}={g['group']}", "metric": "calibration_in_the_large", "value": g["gap"], "limit": g["gap_margin_95"]})
            if best_auc - g["auc"] > RISK_AUC_GAP:
                flags.append({"area": "risk", "group": f"{feature}={g['group']}", "metric": "auc_gap", "value": r(best_auc - g["auc"]), "limit": RISK_AUC_GAP})
            if g["ece"] > RISK_ECE_MAX:
                flags.append({"area": "risk", "group": f"{feature}={g['group']}", "metric": "ece", "value": g["ece"], "limit": RISK_ECE_MAX})
    watch = [{"group": f"{f}={e['group']}", "ece": e["ece"], "ece_noise_p95": e["ece_noise_p95"]}
             for f, rows in out.items() for e in rows if e["ece_above_noise"]]
    return {"source": {"file": rel(RISK_JSON), "sha256": sha256(RISK_JSON), "scenario": risk["meta"]["scenario"],
                       "n_heldout": risk["meta"]["n_heldout"], "excluded_features": risk["meta"]["excluded_features"], "synthetic": True},
            "heldout_overall": {k: risk["heldout"][k] for k in ("auc", "ece", "brier", "n")},
            "groups": out, "flags": flags, "watch": watch}


# =============================================================================================== 3. validator

ARABIC = re.compile("[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
LATIN = re.compile("[A-Za-z]")
# Hand-labelled: the Latin-script attacks that are Arabic words written in Latin letters (Arabizi). Every other
# Latin-only attack is English or language-neutral (digits, links, placeholders).
ARABIZI_ATTACKS = {"RT-V-02.6", "RT-V-02.7", "RT-V-02.8", "RT-V-02.9", "RT-V-02.10", "RT-V-02.18"}
CONTENT_ATTACKS = {f"RT-V-0{n}" for n in range(1, 8)}  # disease words, look-alikes, spacing, negation, result hints
EXTRA_NAMES = {"en": ["Dr Omar Al Mansoori", "Nurse Fatima Ali", "Dr Priya Nair"],
               "ar": ["الدكتور عمر المنصوري", "الممرضة فاطمة علي", "الدكتورة بريا ناير"]}


def attack_language(cid: str, attack: str, base: str) -> str:
    if cid in ARABIZI_ATTACKS:
        return "arabizi"
    if ARABIC.search(attack) and LATIN.search(attack):
        return "mixed"
    if ARABIC.search(attack) or base == "ar":
        return "ar"
    return "en"


def validator_section() -> dict:
    import test_rt_validator as rt  # tests/redteam/test_rt_validator.py: the attack lists and the request builder
    from app.ai import provider as prov
    from app.ai.templates import SAMPLE_VALUES, render
    from app.ai.validate import validate

    attacks: dict[str, dict] = {}
    content: dict[str, dict] = {}
    misses = []
    for abuse, items in rt.ATTACKS.items():
        for n, (attack, lang) in enumerate(items, start=1):
            cid = f"{abuse}.{n}"
            group = attack_language(cid, attack, lang)
            req = rt.make_req("ar" if lang == "ar" else "en")
            ok = validate(rt.msg(attack, lang), req).ok
            for table, use in ((attacks, True), (content, abuse in CONTENT_ATTACKS)):
                if use:
                    t = table.setdefault(group, {"n": 0, "accepted": 0})
                    t["n"] += 1
                    t["accepted"] += int(ok)
            if ok:
                misses.append({"id": cid, "language": group, "known_limitation": cid in rt.LIMITATION})

    benign: dict[str, dict] = {}
    rejected = []

    def control(lang: str, label: str, text: str, req) -> None:
        res = validate(text, req)
        b = benign.setdefault(lang, {"n": 0, "rejected": 0})
        b["n"] += 1
        if not res.ok:
            b["rejected"] += 1
            rejected.append({"control": label, "language": lang, "reasons": list(res.reasons)})

    for lang in ("en", "ar"):
        names = [SAMPLE_VALUES[lang]["owner_name"], *EXTRA_NAMES[lang]]
        for channel in prov.CHANNEL_MAX_CHARS:
            for purpose in prov.PURPOSES:
                for name in names:
                    for dated in (False, True):
                        date = SAMPLE_VALUES[lang]["appointment_date"] if (dated or purpose == "appointment_confirmation") else None
                        req = prov.DraftRequest(purpose, channel, lang, name, "primary_care_owner", SAMPLE_VALUES[lang]["next_step"], "colorectal_fit", date)
                        text = render(purpose, channel, lang, owner_name=name, next_step=req.next_step, appointment_date=date)
                        control(lang, f"template:{purpose}.{channel}:{'dated' if date else 'undated'}", text, req)
            text = rt._padded(lang, channel, prov.CHANNEL_MAX_CHARS[channel])
            control(lang, f"at_limit:{channel}", text, rt.make_req(lang, channel=channel))
        control(lang, "good_message", rt.GOOD[lang], rt.make_req(lang))

    def rates(table: dict, key: str) -> dict:
        return {g: {"n": v["n"], key: v[key], "rate": r(v[key] / v["n"]), "ci": wilson(v[key], v["n"]),
                    "too_few": v["n"] < MIN_GROUP_N} for g, v in sorted(table.items())}

    fn, fn_content, fp = rates(attacks, "accepted"), rates(content, "accepted"), rates(benign, "rejected")
    flags = []
    for g, v in fn.items():
        if v["rate"] > VALIDATOR_FN_MAX:
            flags.append({"area": "validator", "group": g, "metric": "false_negative_rate", "value": v["rate"], "limit": VALIDATOR_FN_MAX})
        if v["too_few"]:
            flags.append({"area": "validator", "group": g, "metric": "too_few_attacks", "value": v["n"], "limit": MIN_GROUP_N})
    for g, v in fp.items():
        if v["rate"] > VALIDATOR_FP_MAX:
            flags.append({"area": "validator", "group": g, "metric": "false_positive_rate", "value": v["rate"], "limit": VALIDATOR_FP_MAX})
    flags.append({"area": "validator", "group": "arabizi", "metric": "no_benign_controls", "value": 0, "limit": 1,
                  "note": "outbound drafts are English or Arabic only, so there is no Arabizi message to accept"})
    return {"source": {"attacks": "tests/redteam/test_rt_validator.py (ATTACKS)", "validator": "api/app/ai/validate.py",
                       "arabizi_labels": sorted(ARABIZI_ATTACKS, key=lambda s: (s.split(".")[0], int(s.split(".")[1])))},
            "false_negative_by_language": fn, "false_negative_content_attacks_by_language": fn_content,
            "false_positive_by_language": fp, "accepted_attacks": misses, "rejected_benign": rejected, "flags": flags}


# =============================================================================================== report

MITIGATIONS = {
    ("triage", "auto_precision"): "In place: the automatic step for an auto-handled reply is non-clinical (a callback or review request, or nothing for `confirm`), an opt-out never depends on the model (deterministic gate first, then `opt_out` is always human), and every reply stays readable on the case page. Proposed, not shipped (the engine is outside this ticket): the stricter per-group threshold in section 1, or every reply of the group to a person; both need the group detected at run time.",
    ("triage", "always_human_auto_handled"): "A question or an unclear reply was read as a routine intent and got the automatic step instead of a person. In place: the reply is on the case page and the case keeps its SLA clock and escalation ladder, so silence still escalates. Proposed: the per-group threshold in section 1; coordinators told (Why? panel) that a routine label is a model reading, not a fact.",
    ("triage", "macro_recall_gap"): "In place: most misreads cost a person's time rather than safety, because `question`, `other` and `opt_out` always go to a person and low-confidence replies abstain. Planned: real Arabizi and Gulf-dialect replies, reviewed by a native speaker (H2), in the next training set (retraining policy in docs/governance/ai-incidents.md).",
    ("triage", "opt_out_not_to_person"): "A defect: an opt-out must always reach a person. Block the release.",
    ("validator", "false_negative_rate"): "In place: every draft is read and approved by a person before it can be sent (`approved_unsent`, model check I3); the accepted attacks are known limitations HZ-31 (social engineering and plain-words disclosure that no word list can hold).",
    ("validator", "too_few_attacks"): "Gap in the test set, not in the validator: add Arabizi and mixed-script attacks to tests/redteam (the generated variants of `make redteam-generate` are the route).",
    ("validator", "false_positive_rate"): "A benign template rejected is a defect: fix the template or the phrase list before release.",
    ("validator", "no_benign_controls"): "Not applicable today: no outbound text is in Arabizi. If an Arabizi channel is added, add benign controls first.",
    ("risk", "calibration_in_the_large"): "Retrain or recalibrate before use; the score only orders the worklist meanwhile.",
    ("risk", "auc_gap"): "The score only orders the worklist and never changes a due date or an escalation; a person decides who to call first.",
    ("risk", "ece"): "Recalibrate before use.",
}


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def ci(c: list[float]) -> str:
    return f"{pct(c[0])} to {pct(c[1])}"


def render_md(res: dict) -> str:
    t, rk, v = res["triage"], res["risk"], res["validator"]
    L = ["# Fairness audit (M7-5)", "",
         "Generated by `tools/rai/fairness.py` (`make rai-fairness`; `make rai-fairness-check` fails if this file differs from a fresh run). "
         "Do not edit by hand.", "",
         "**All data are synthetic.** The triage replies are developer-written templates, the risk-score cohort is simulated and the "
         "red-team set is self-authored. These numbers show how the shipped models and rules behave on those sets, split by language. "
         "They are not evidence about real patients. No native Arabic speaker and no clinician has reviewed the data yet (H1, H2).", "",
         "## Summary of flags", "",
         "| Area | Group | Metric | Value | Limit | Mitigation |", "|---|---|---|---|---|---|"]
    for f in res["flags"]:
        val = f["value"] if isinstance(f["value"], int) else pct(f["value"]) if f["metric"] not in ("auc_gap",) else f"{f['value']:.3f}"
        lim = f["limit"] if isinstance(f["limit"], int) else pct(f["limit"]) if f["metric"] not in ("auc_gap",) else f"{f['limit']:.3f}"
        L.append(f"| {f['area']} | {f['group']} | {f['metric']} | {val} | {lim} | {MITIGATIONS[(f['area'], f['metric'])]} |")
    if not res["flags"]:
        L.append("| - | - | - | - | - | none |")

    L += ["", "## 1. Reply triage by language", "",
          f"Deployed path replayed on the test split: opt-out gate, then the model `{t['source']['model_id']}` with temperature and "
          f"threshold {t['source']['threshold']} from `{t['source']['calibration']}`. The recorded scores reproduce "
          f"`{t['integrity']['reproduces']}` on {t['integrity']['quantities_checked']} quantities before anything below is computed. "
          "Precision and recall are macro averages over the intents present in the group. Auto-handled precision is the share of replies "
          "the system acted on without a person that it read correctly (target 95%).", "",
          "| Group | n | Accuracy | Macro precision | Macro recall | Auto-handled | Auto-handled precision (95% CI) | Wrong automatic steps | Always-human intents to a person | Opt-outs to a person (by gate) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, g in [*t["by_language"].items(), *(("dialect " + k, x) for k, x in t["by_dialect"].items())]:
        L.append(f"| {name} | {g['n']} | {pct(g['accuracy'])} | {pct(g['macro_precision'])} | {pct(g['macro_recall'])} | "
                 f"{g['auto_handled_n']} ({pct(g['coverage'])}) | {pct(g['auto_precision'])} ({ci(g['auto_precision_ci'])}) | "
                 f"{g['wrong_automatic_steps']} | {pct(g['always_human_to_person'])} | {g['opt_out_to_person']} of {g['opt_out_n']} ({g['opt_out_caught_by_gate']}) |")
    L += ["", "Per-intent recall by language:", "", "| Intent | " + " | ".join(k for k in t["by_language"] if k != "all") + " |",
          "|---|" + "---|" * (len(t["by_language"]) - 1)]
    for intent in INTENTS:
        cells = []
        for k, g in t["by_language"].items():
            if k == "all":
                continue
            p = g["per_intent"].get(intent)
            cells.append(f"{pct(p['recall'])} (n={p['support']})" if p else "-")
        L.append(f"| {intent} | " + " | ".join(cells) + " |")
    for p in t["proposed_mitigations"]:
        L += ["", f"**Proposed mitigation for {p['group']} (not shipped).** "
              + (f"A threshold of {p['threshold']} for this group (deployed: {p['deployed_threshold']}), chosen on the validation split "
                 f"({p['validation_n']} replies) for {pct(VAL_PRECISION_TARGET)} precision, gives {pct(p['test_auto_precision'])} auto-handled precision "
                 f"({ci(p['test_auto_precision_ci'])}) on the test split at {pct(p['test_coverage'])} coverage "
                 f"({p['test_auto_handled_n']} replies). It needs the API to tell this group apart at run time, which it does not do today; "
                 "the alternative is to send every reply of the group to a person."
                 if p.get("threshold") is not None else p["note"])]  # noqa: E501
    for name, g in t["by_language"].items():
        if name != "all" and g["always_human_auto_handled"]:
            L += ["", f"Always-human replies acted on without a person ({name}, label->read as): " + ", ".join(g["always_human_auto_handled"]) + "."]

    L += ["", "## 2. Risk score by subgroup", "",
          f"From `{rk['source']['file']}` (scenario `{rk['source']['scenario']}`, {rk['source']['n_heldout']} held-out simulated cases; "
          f"`make risk-check` proves the file equals a fresh retrain). Language is excluded as a feature and audited as an outcome group. "
          f"Overall held-out AUC {rk['heldout_overall']['auc']:.3f}, ECE {rk['heldout_overall']['ece']:.3f}.", "",
          "| Subgroup | n | AUC | AUC gap to best | ECE | ECE noise p95 | Mean predicted | Observed | Gap | Gap margin (95%) | Flag |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for feature, rows in rk["groups"].items():
        for e in rows:
            flag = "FLAG" if e["gap_flag"] else ("watch: ECE above noise" if e["ece_above_noise"] else "ok")
            L.append(f"| {feature}={e['group']} | {e['n']} | {e['auc']:.3f} | {e['auc_gap_to_best']:.3f} | {e['ece']:.3f} | {e['ece_noise_p95']:.3f} | "
                     f"{e['mean_predicted']:.3f} | {e['prevalence']:.3f} | {e['gap']:+.3f} | {e['gap_margin_95']:.3f} | {flag} |")
    if rk["watch"]:
        L += ["", "Watch (not a flag): " + "; ".join(f"{w['group']} ECE {w['ece']:.3f} is above the p95 of its sampling noise ({w['ece_noise_p95']:.3f}) "
                                                   "but within the 0.05 tolerance" for w in rk["watch"]) + ". Re-check on the first real data."]
    L += ["", "Intersections (language by channel) are not reported: `risk.json` holds one-way groups only. Gap noted for the pilot.", ""]

    L += ["## 3. Output validator by language", "",
          "False negatives: red-team attacks (`tests/redteam/test_rt_validator.py`) the validator ACCEPTED. Language is the script of the attack "
          "(Arabizi hand-labelled: " + ", ".join(v["source"]["arabizi_labels"]) + "; mixed = Arabic and Latin letters in one attack; "
          "language-neutral attacks such as digits, links and placeholders count under their message's language). False positives: benign "
          "messages REJECTED (every template in every purpose and channel, rendered with four owner names, with and without a date, plus the "
          "channel at-limit texts and the good message).", "",
          "| Language | Attacks | Accepted (FN) | FN rate (95% CI) | Content attacks only (RT-V-01 to 07): FN rate | Benign | Rejected (FP) | FP rate (95% CI) |",
          "|---|---|---|---|---|---|---|---|"]
    for g in sorted(set(v["false_negative_by_language"]) | set(v["false_positive_by_language"])):
        a, c, b = v["false_negative_by_language"].get(g), v["false_negative_content_attacks_by_language"].get(g), v["false_positive_by_language"].get(g)
        L.append(f"| {g} | {a['n'] if a else 0} | {a['accepted'] if a else '-'} | {pct(a['rate']) + ' (' + ci(a['ci']) + ')' if a else '-'} | "
                 f"{pct(c['rate']) + ' of ' + str(c['n']) if c else '-'} | {b['n'] if b else 0} | {b['rejected'] if b else '-'} | "
                 f"{pct(b['rate']) + ' (' + ci(b['ci']) + ')' if b else 'none (no outbound text in this script)'} |")
    L += ["", "Accepted attacks: " + (", ".join(f"{m['id']} ({m['language']}{', known limitation HZ-31' if m['known_limitation'] else ''})" for m in v["accepted_attacks"]) or "none") + ".",
          "", "Rejected benign messages: " + (", ".join(f"{x['control']} ({x['language']}: {','.join(x['reasons'])})" for x in v["rejected_benign"]) or "none") + ".", "",
          "## What this does not show", "",
          "- Performance on real patients' replies, real dialect spread (Emirati, Levantine, Egyptian, South Asian English) or real Arabizi spelling.",
          "- Intersectional groups (for example Arabizi on WhatsApp, or age by language) for the risk score.",
          "- Whether the Arabic templates read naturally: that is the native-speaker review (H2), not a metric.", ""]
    return "\n".join(L)


def build() -> tuple[str, str]:
    res = {"ticket": "M7-5", "synthetic_data_only": True,
           "thresholds": {"auto_precision_target": AUTO_PRECISION_TARGET, "macro_recall_gap": MACRO_RECALL_GAP, "risk_auc_gap": RISK_AUC_GAP,
                          "risk_ece_max": RISK_ECE_MAX, "validator_fn_max": VALIDATOR_FN_MAX, "validator_fp_max": VALIDATOR_FP_MAX, "min_group_n": MIN_GROUP_N},
           "triage": triage_section(), "risk": risk_section(), "validator": validator_section()}
    res["flags"] = [*res["triage"]["flags"], *res["risk"]["flags"], *res["validator"]["flags"]]
    return json.dumps(res, indent=2, ensure_ascii=False, sort_keys=True) + "\n", render_md(res)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed files differ from a fresh run")
    args = ap.parse_args(argv)
    js, md = build()
    js2, md2 = build()  # determinism: two builds in one process must agree byte for byte
    if (js, md) != (js2, md2):
        print("rai-fairness: FAIL (two runs differ)")
        return 1
    if args.check:
        stale = [rel(p) for p, text in ((OUT_JSON, js), (OUT_MD, md)) if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print(f"rai-fairness: FAIL ({', '.join(stale)} differ from a fresh run; run `make rai-fairness`)")
            return 1
        print(f"rai-fairness: PASS ({len(json.loads(js)['flags'])} flags, files up to date)")
        return 0
    OUT_JSON.write_text(js, encoding="utf-8")
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"rai-fairness: wrote {rel(OUT_JSON)} and {rel(OUT_MD)} ({len(json.loads(js)['flags'])} flags)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
