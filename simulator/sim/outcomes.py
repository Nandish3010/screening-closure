"""Health outcomes from completed versus missed colonoscopies (ticket M7-3): a post-processor, not part of the engine.

It reads only the simulator's per-case records (`CaseRecord.t_complete`, `language`, `channel`, `messy`, `in_reach`) through the
public runner (`load_context`, `make_patients`, `run_arm`), the same replications and seeds as `make evaluate`, and turns the
time from positive FIT to colonoscopy into health outcomes with the published values in simulator/params.outcomes.yaml:

  1. Each case falls in one delay group: colonoscopy within 270 days ("prompt"; Corley 2017 found no significant excess risk up
     to 9 months), at 271-360 days ("late", Corley's 10-12 months) or none within the 360-day follow-up ("none", given
     Corley's beyond-12-months risks: it completes later or presents with symptoms).
  2. Any-CRC and advanced-stage (III-IV) risk per group: the prompt risks, shifted on the odds scale by Corley's adjusted odds
     ratios for the late groups.
  3. Early cancers (I-II) and advanced cancers (III-IV, split into III and IV) get 5-year survival by stage; deaths within 5
     years follow.
  4. A death avoided gains the remaining UAE life expectancy at diagnosis minus the years a CRC decedent lives; life-years are
     discounted at 3% and weighted by a population utility for QALYs.
  5. Costs (illustrative): the per-case fee and the extra colonoscopies, less the first-year treatment cost avoided by the
     stage shift (the business case's inputs). Cost per QALY = net cost / QALYs.

Uncertainty is a probabilistic sensitivity analysis (PSA): each draw samples every parameter from its distribution and one
replication of the simulation (so simulation noise is carried too). Results are reported per 1,000 FIT-positive cases and per
year for Abu Dhabi (17,696 positives), for the literature-anchored headline, the Abu Dhabi-shaped baseline overlay, the upper
range and, as a sensitivity, the literature-anchored scenario at base colonoscopy capacity. Equity: the same outcomes by
language, preferred channel, contact details on file and programme reach.

    python -m simulator.sim.outcomes [--reps 20 --cases 2000 --days 360 --seed 20261017 --jobs 8] [--out-dir docs/results] [--check]

outcomes.json has sorted keys, floats rounded to 6 places and no timestamps; outcomes.md is rendered from it. Both are
byte-for-byte reproducible for the same seed, sizes and parameters (`--jobs` never changes a number); `--check` reruns
and compares both files with the committed ones (the tests check that `--jobs` never changes a byte).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import yaml

from . import DEFAULT_CONSERVATIVE, DEFAULT_PARAMS, REPO_ROOT
from .engine import ARMS, BASELINE, ORCHESTRATED
from .evaluate import rep_seed
from .metrics import percentile
from .rng import stream
from .runner import load_context, make_patients, run_arm

OUTCOME_PARAMS = REPO_ROOT / "simulator" / "params.outcomes.yaml"
ABUDHABI = REPO_ROOT / "simulator" / "params.abudhabi.yaml"
CAPACITY = REPO_ROOT / "simulator" / "params.capacity.yaml"
SCHEMA_VERSION = 1
DEFAULT_SEED = 20261017

HEADLINE, ABU, UPPER, CAP = "literature_anchored", "abudhabi_literature_anchored", "upper_range", "literature_anchored_base_capacity"
SCENARIO_ORDER = (HEADLINE, ABU, UPPER, CAP)
SCENARIO_LABEL = {
    HEADLINE: "Literature anchored (headline)",
    ABU: "Abu Dhabi-shaped baseline, literature anchored (sensitivity)",
    UPPER: "Upper range (mechanism ceiling)",
    CAP: "Literature anchored at base colonoscopy capacity (sensitivity)",
}
BINS = ("prompt", "late", "none")
GROUP_DIMS = (("language", ("ar", "en", "other")), ("channel", ("sms", "whatsapp", "phone", "letter")),
              ("contact", ("valid", "wrong")), ("reach", ("in", "out")))
PARAM_NAMES = ("crc_risk_ref", "adv_fraction_ref", "or_any_10_12m", "or_adv_10_12m", "or_any_over_12m", "or_adv_over_12m",
               "aa_yield", "surv5_localized", "surv5_regional", "surv5_distant", "stage4_share_of_advanced", "age_at_diagnosis",
               "years_to_death_if_crc_death", "utility_population", "colonoscopy_cost_aed", "treatment_gap_aed",
               "stage_pair_factor")
# headline metrics (orchestrated minus baseline, per 1,000 FIT-positive cases; positive = benefit)
KEY_METRICS = ("extra_colonoscopies", "extra_crc_detected", "extra_aa_removed", "cancers_avoided", "advanced_avoided",
               "deaths_avoided_5y", "life_years", "life_years_disc", "qalys_disc", "net_cost_aed", "cost_per_qaly_aed")


def scenario_stacks() -> dict[str, list[Path]]:
    """The parameter-file stacks, exactly as `make evaluate`, `make evaluate-abudhabi` and `make evaluate-capacity` layer them."""
    return {
        HEADLINE: [DEFAULT_PARAMS, DEFAULT_CONSERVATIVE],
        ABU: [DEFAULT_PARAMS, ABUDHABI, DEFAULT_CONSERVATIVE],
        UPPER: [DEFAULT_PARAMS],
        CAP: [DEFAULT_PARAMS, DEFAULT_CONSERVATIVE, CAPACITY],
    }


# ------------------------------------------------------------------------------------------------ per-case extraction

def delay_bin(t_complete: float | None, prompt_max: float, late_max: float) -> str:
    if t_complete is None or t_complete > late_max:
        return "none"
    return "prompt" if t_complete <= prompt_max else "late"


def group_keys(rec) -> list[str]:
    return ["all", f"language:{rec.language}", f"channel:{rec.channel}",
            "contact:wrong" if "wrong_contact" in rec.messy else "contact:valid", "reach:in" if rec.in_reach else "reach:out"]


def bin_counts(records, prompt_max: float, late_max: float) -> dict[str, list[int]]:
    """{group: [n, prompt, late, none]} for one arm of one replication."""
    out: dict[str, list[int]] = {}
    for r in records:
        b = BINS.index(delay_bin(r.t_complete, prompt_max, late_max))
        for g in group_keys(r):
            row = out.setdefault(g, [0, 0, 0, 0])
            row[0] += 1
            row[1 + b] += 1
    return dict(sorted(out.items()))


def _run_rep(job: tuple) -> dict:
    stacks, rep, seed, cases, days, prompt_max, late_max = job
    out = {"rep": rep, "seed": seed, "scenarios": {}}
    for name, stack in stacks:
        ctx = load_context(stack, warn=False)
        patients = make_patients(ctx, cases, seed)
        out["scenarios"][name] = {arm: bin_counts(run_arm(ctx, arm, cases, days, seed, patients).records, prompt_max, late_max)
                                  for arm in ARMS}
    return out


def simulate(reps: int, cases: int, days: int, seed: int, jobs: int, cfg: dict, scenarios: tuple = SCENARIO_ORDER) -> list[dict]:
    if days < cfg["delay_bins_days"]["late_max"]:
        raise ValueError(f"follow-up of {days} days is shorter than the late bin ({cfg['delay_bins_days']['late_max']} days)")
    all_stacks = scenario_stacks()
    stacks = [(n, [str(p) for p in all_stacks[n]]) for n in scenarios]
    pm, lm = cfg["delay_bins_days"]["prompt_max"], cfg["delay_bins_days"]["late_max"]
    job_list = [(stacks, i, rep_seed(seed, i), cases, days, pm, lm) for i in range(reps)]
    if jobs > 1 and reps > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, reps)) as ex:
            return list(ex.map(_run_rep, job_list))
    return [_run_rep(j) for j in job_list]


# ------------------------------------------------------------------------------------------------ parameters

def load_outcome_params(path: Path = OUTCOME_PARAMS) -> dict:
    return yaml.safe_load(Path(path).read_text())


def central(cfg: dict) -> dict:
    p = {n: float(cfg[n]["value"]) for n in PARAM_NAMES}
    p.update(_fixed(cfg))
    return p


def _fixed(cfg: dict) -> dict:
    return {"life_expectancy": {int(k): float(v) for k, v in cfg["life_expectancy_uae"]["value"].items()},
            "discount_rate": float(cfg["discount_rate"]["value"]), "fee_per_case_aed": float(cfg["fee_per_case_aed"]["value"]),
            "positives_per_year": float(cfg["positives_per_year_abu_dhabi"]["value"])}


def _draw_one(spec: dict, rng) -> float:
    dist, v, a = spec.get("dist", "fixed"), float(spec["value"]), spec.get("args")
    if dist == "fixed":
        return v
    if dist == "uniform":
        return rng.uniform(a[0], a[1])
    if dist == "triangular":
        return rng.triangular(a[0], a[2], a[1])  # random.triangular(low, high, mode)
    if dist == "beta":
        return rng.betavariate(a[0], a[1])
    if dist == "beta_ci":
        sd = (a[1] - a[0]) / 3.92
        k = v * (1 - v) / sd ** 2 - 1
        return rng.betavariate(v * k, (1 - v) * k)
    if dist == "lognormal_ci":
        return rng.lognormvariate(math.log(v), (math.log(a[1]) - math.log(a[0])) / 3.92)
    raise ValueError(f"unknown dist {dist!r}")


def draw(cfg: dict, rng) -> dict:
    p = {n: _draw_one(cfg[n], rng) for n in PARAM_NAMES}  # fixed order: the stream is consumed identically every run
    p.update(_fixed(cfg))
    return p


# ------------------------------------------------------------------------------------------------ the outcomes model

def odds_shift(p: float, odds_ratio: float) -> float:
    o = p / (1 - p) * odds_ratio
    return o / (1 + o)


def bin_risks(p: dict) -> dict[str, tuple[float, float]]:
    """{bin: (any-CRC risk, advanced-stage risk)}; advanced never exceeds any."""
    c0 = p["crc_risk_ref"]
    a0 = c0 * p["adv_fraction_ref"]
    c1, c2 = odds_shift(c0, p["or_any_10_12m"]), odds_shift(c0, p["or_any_over_12m"])
    a1, a2 = min(odds_shift(a0, p["or_adv_10_12m"]), c1), min(odds_shift(a0, p["or_adv_over_12m"]), c2)
    return {"prompt": (c0, a0), "late": (c1, a1), "none": (c2, a2)}


def life_expectancy(age: float, table: dict[int, float]) -> float:
    ages = sorted(table)
    if age <= ages[0]:
        return table[ages[0]] - (age - ages[0])  # outside the table only by a little in practice; linear
    for lo, hi in zip(ages, ages[1:]):
        if age <= hi:
            return table[lo] + (table[hi] - table[lo]) * (age - lo) / (hi - lo)
    return table[ages[-1]]


def life_years_per_death_avoided(p: dict) -> tuple[float, float]:
    """(undiscounted, discounted) life-years gained when one CRC death within 5 years is avoided."""
    ex = life_expectancy(p["age_at_diagnosis"], p["life_expectancy"])
    d = p["years_to_death_if_crc_death"]
    undiscounted = max(ex - d, 0.0)
    delta = math.log(1 + p["discount_rate"])
    discounted = (math.exp(-delta * d) - math.exp(-delta * max(ex, d))) / delta if delta > 0 else undiscounted
    return undiscounted, discounted


def arm_outcomes(shares: dict[str, float], p: dict) -> dict:
    """Per 1,000 FIT-positive cases for one arm, from the shares of cases in each delay group."""
    risks = bin_risks(p)
    s_early, s_adv = p["surv5_localized"], (1 - p["stage4_share_of_advanced"]) * p["surv5_regional"] \
        + p["stage4_share_of_advanced"] * p["surv5_distant"]
    cancers = 1000 * sum(shares[b] * risks[b][0] for b in BINS)
    adv = 1000 * sum(shares[b] * risks[b][1] for b in BINS)
    early = cancers - adv
    colonoscopies = 1000 * (shares["prompt"] + shares["late"])
    return {
        "colonoscopies": colonoscopies,
        "crc_detected_by_colonoscopy": 1000 * (shares["prompt"] * risks["prompt"][0] + shares["late"] * risks["late"][0]),
        "aa_removed": colonoscopies * p["aa_yield"],
        "cancers": cancers, "advanced": adv, "early": early,
        "stage_III": adv * (1 - p["stage4_share_of_advanced"]), "stage_IV": adv * p["stage4_share_of_advanced"],
        "deaths_5y": early * (1 - s_early) + adv * (1 - s_adv),
    }


def compare(base_shares: dict, orch_shares: dict, p: dict) -> dict:
    """Orchestrated minus baseline per 1,000 positives (signed so that a positive number is a benefit) and the costs."""
    b, o = arm_outcomes(base_shares, p), arm_outcomes(orch_shares, p)
    ly, ly_disc = life_years_per_death_avoided(p)
    deaths = b["deaths_5y"] - o["deaths_5y"]
    adv_avoided = b["advanced"] - o["advanced"]
    extra_col = o["colonoscopies"] - b["colonoscopies"]
    qalys = deaths * ly_disc * p["utility_population"]
    avoided_tx = adv_avoided * p["treatment_gap_aed"] * p["stage_pair_factor"]
    net = 1000 * p["fee_per_case_aed"] + extra_col * p["colonoscopy_cost_aed"] - avoided_tx
    return {
        "extra_colonoscopies": extra_col,
        "extra_crc_detected": o["crc_detected_by_colonoscopy"] - b["crc_detected_by_colonoscopy"],
        "extra_aa_removed": o["aa_removed"] - b["aa_removed"],
        "cancers_avoided": b["cancers"] - o["cancers"],
        "advanced_avoided": adv_avoided,
        "deaths_avoided_5y": deaths,
        "life_years": deaths * ly, "life_years_disc": deaths * ly_disc, "qalys_disc": qalys,
        "programme_cost_aed": 1000 * p["fee_per_case_aed"], "colonoscopy_cost_aed": extra_col * p["colonoscopy_cost_aed"],
        "treatment_cost_avoided_aed": avoided_tx, "net_cost_aed": net,
        "cost_per_qaly_aed": net / qalys if qalys > 0 else None,
        "baseline": b, "orchestrated": o,
    }


def shares_of(row: list[int]) -> dict[str, float]:
    n = row[0]
    return {b: row[1 + i] / n for i, b in enumerate(BINS)} if n else {b: 0.0 for b in BINS}


def pooled(reps: list[dict], scenario: str, arm: str, group: str) -> list[int]:
    tot = [0, 0, 0, 0]
    for r in reps:
        row = r["scenarios"][scenario][arm].get(group, [0, 0, 0, 0])
        tot = [a + b for a, b in zip(tot, row)]
    return tot


def _spearman(x: list[float], y: list[float]) -> float | None:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: (v[i], i))
        r = [0.0] * len(v)
        for k, i in enumerate(order):
            r[i] = float(k)
        return r
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else None


def _summ(vals: list[float]) -> dict:
    return {"mean": sum(vals) / len(vals), "p025": percentile(vals, 0.025), "p975": percentile(vals, 0.975),
            "median": percentile(vals, 0.5)}


def analyse(reps: list[dict], cfg: dict, seed: int) -> dict:
    """Central (deterministic) results from replication-pooled shares, then the PSA over parameters and replications."""
    scenarios = [s for s in SCENARIO_ORDER if s in reps[0]["scenarios"]]
    groups = sorted({g for r in reps for s in scenarios for a in ARMS for g in r["scenarios"][s][a]})
    pc = central(cfg)
    per_year = pc["positives_per_year"] / 1000
    gdp_aed = float(cfg["gdp_per_capita_usd"]["value"]) * float(cfg["usd_aed"]["value"])
    out: dict = {"scenarios": {}}
    draws = int(cfg["psa"]["draws"])
    rng = stream(seed, cfg["psa"]["seed_label"])
    plan = []
    for _ in range(draws):
        plan.append((draw(cfg, rng), rng.randrange(len(reps))))
    for s in scenarios:
        sc: dict = {"groups": {}}
        for g in groups:
            rows = {a: pooled(reps, s, a, g) for a in ARMS}
            if rows[BASELINE][0] == 0 or rows[ORCHESTRATED][0] == 0:
                continue
            sh = {a: shares_of(rows[a]) for a in ARMS}
            cen = compare(sh[BASELINE], sh[ORCHESTRATED], pc)
            psa_vals: dict[str, list[float]] = {k: [] for k in KEY_METRICS if k != "cost_per_qaly_aed"}
            icers, below1, below3, saving = [], 0, 0, 0
            for p, ri in plan:
                rr = reps[ri]["scenarios"][s]
                bs, os_ = shares_of(rr[BASELINE].get(g, [0, 0, 0, 0])), shares_of(rr[ORCHESTRATED].get(g, [0, 0, 0, 0]))
                c = compare(bs, os_, p)
                for k in psa_vals:
                    psa_vals[k].append(c[k])
                if c["qalys_disc"] > 0:
                    icers.append(c["cost_per_qaly_aed"])
                    below1 += c["cost_per_qaly_aed"] <= gdp_aed
                    below3 += c["cost_per_qaly_aed"] <= 3 * gdp_aed
                saving += c["net_cost_aed"] < 0
            entry = {
                "n_cases": {a: rows[a][0] for a in ARMS},
                "shares": sh,
                "central": {k: v for k, v in cen.items() if k not in ("baseline", "orchestrated")},
                "arms_central": {"baseline": cen["baseline"], "orchestrated": cen["orchestrated"]},
                "psa": {k: _summ(v) for k, v in psa_vals.items()},
                "psa_share_deaths_avoided_positive": sum(1 for v in psa_vals["deaths_avoided_5y"] if v > 0) / draws,
                "psa_cost_per_qaly": {**(_summ(icers) if icers else {}), "draws_with_qaly_gain": len(icers),
                                      "share_below_1x_gdp_per_capita": below1 / draws,
                                      "share_below_3x_gdp_per_capita": below3 / draws, "share_cost_saving": saving / draws},
            }
            if g == "all":
                entry["per_year_abu_dhabi"] = {
                    "central": {k: cen[k] * per_year for k in KEY_METRICS if k != "cost_per_qaly_aed"},
                    "psa": {k: {q: v * per_year for q, v in _summ(vals).items()} for k, vals in psa_vals.items()},
                }
                entry["drivers_deaths_avoided"] = {
                    n: _spearman([p[n] for p, _ in plan], psa_vals["deaths_avoided_5y"]) for n in PARAM_NAMES}
                entry["drivers_deaths_avoided"]["simulation_replication"] = _spearman(
                    [compare(shares_of(reps[ri]["scenarios"][s][BASELINE]["all"]), shares_of(reps[ri]["scenarios"][s][ORCHESTRATED]["all"]), pc)["deaths_avoided_5y"] for _, ri in plan],
                    psa_vals["deaths_avoided_5y"])
            sc["groups"][g] = entry
        out["scenarios"][s] = sc
    risks = bin_risks(pc)
    s_adv = (1 - pc["stage4_share_of_advanced"]) * pc["surv5_regional"] + pc["stage4_share_of_advanced"] * pc["surv5_distant"]
    ly, ly_disc = life_years_per_death_avoided(pc)
    out["model_central"] = {
        "risk_per_1000": {b: {"any_crc": 1000 * risks[b][0], "advanced": 1000 * risks[b][1],
                              "advanced_share_of_cancers": risks[b][1] / risks[b][0]} for b in BINS},
        "surv5_early": pc["surv5_localized"], "surv5_advanced": s_adv,
        "life_years_per_death_avoided": ly, "life_years_per_death_avoided_disc": ly_disc,
        "gdp_per_capita_aed": gdp_aed,
    }
    return out


def params_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _round(obj):
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {str(k): _round(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    return obj


def build(reps_n: int, cases: int, days: int, seed: int, jobs: int, cfg: dict) -> dict:
    reps = simulate(reps_n, cases, days, seed, jobs, cfg)
    res = analyse(reps, cfg, seed)
    res["meta"] = {"schema_version": SCHEMA_VERSION, "seed": seed, "replications": reps_n, "cases_per_replication": cases,
                   "follow_up_days": days, "psa_draws": int(cfg["psa"]["draws"]), "outcome_params_hash": params_hash(cfg),
                   "scenario_params_files": {s: [str(Path(p).relative_to(REPO_ROOT)) for p in v] for s, v in scenario_stacks().items()},
                   "delay_bins_days": cfg["delay_bins_days"]}
    res["per_replication"] = [{"rep": r["rep"], "seed": r["seed"], "all": {s: r["scenarios"][s] and
                               {a: r["scenarios"][s][a]["all"] for a in ARMS} for s in r["scenarios"]}} for r in reps]
    res["parameters"] = {n: {k: cfg[n].get(k) for k in ("value", "dist", "args", "unit", "status", "source", "url", "accessed")}
                         for n in (*PARAM_NAMES, "life_expectancy_uae", "discount_rate", "fee_per_case_aed",
                                   "positives_per_year_abu_dhabi", "gdp_per_capita_usd", "usd_aed", "never_completer_rule")}
    return _round(res)


# ------------------------------------------------------------------------------------------------ report

def _n(v, d=1):
    if v is None:
        return "n/a"
    return f"{v:,.{d}f}"


def _ci(ps: dict, d=1, scale=1.0) -> str:
    return f"{_n(ps['p025'] * scale, d)} to {_n(ps['p975'] * scale, d)}"


def _aed(v) -> str:
    return "n/a" if v is None else f"AED {v:,.0f}"


LABELS = {
    "extra_colonoscopies": ("Extra colonoscopies within 360 days", 1),
    "extra_crc_detected": ("Extra cancers found at colonoscopy within 360 days", 2),
    "extra_aa_removed": ("Extra advanced adenomas removed", 1),
    "cancers_avoided": ("Cancers avoided (adenomas removed before they progress)", 2),
    "advanced_avoided": ("Advanced-stage (III-IV) cancers avoided", 2),
    "deaths_avoided_5y": ("CRC deaths within 5 years avoided", 2),
    "life_years": ("Life-years gained, undiscounted", 1),
    "life_years_disc": ("Life-years gained, discounted 3%", 1),
    "qalys_disc": ("QALYs gained, discounted 3%", 1),
}


def render_md(res: dict) -> str:
    m, sc = res["meta"], res["scenarios"]
    mc = res["model_central"]
    P = res["parameters"]
    L: list[str] = []
    a = L.append
    a("# Health outcomes: cancers, deaths and QALYs from completed colonoscopies")
    a("")
    a(f"Generated by `make evaluate-outcomes` (`python -m simulator.sim.outcomes`; seed {m['seed']}, {m['replications']} replications x "
      f"{m['cases_per_replication']:,} cases x {m['follow_up_days']} days, {m['psa_draws']:,} PSA draws, outcome parameters hash "
      f"`{m['outcome_params_hash']}`). Machine-readable: [`outcomes.json`](outcomes.json). Byte-for-byte reproducible: "
      "`make evaluate-outcomes-check`.")
    a("")
    a("**Read this first.** All patients are synthetic. The simulator's colonoscopy timing is calibrated, and its effect size "
      "comes from published navigation trials; the outcome figures then come from published observational studies, mostly "
      "outside the UAE. Nothing here was measured in Abu Dhabi, and no clinician has reviewed it. The numbers show the size of "
      "the health stake under stated assumptions. They are not a forecast of lives saved.")
    a("")
    # ---- headline
    h = sc[HEADLINE]["groups"]["all"]
    hy = h["per_year_abu_dhabi"]
    a("## Headline (literature-anchored scenario)")
    a("")
    a(f"Per 1,000 FIT-positive cases, the orchestrated arm avoids about **{_n(h['central']['advanced_avoided'], 1)} advanced-stage cancers** "
      f"(95% uncertainty interval {_ci(h['psa']['advanced_avoided'])}) and **{_n(h['central']['deaths_avoided_5y'], 1)} colorectal cancer deaths "
      f"within 5 years** ({_ci(h['psa']['deaths_avoided_5y'])}), worth **{_n(h['central']['qalys_disc'], 0)} discounted QALYs** "
      f"({_ci(h['psa']['qalys_disc'], 0)}). At Abu Dhabi's central volume of {_n(P['positives_per_year_abu_dhabi']['value'], 0)} positives a year, "
      f"that is about {_n(hy['central']['advanced_avoided'], 0)} advanced cancers ({_ci(hy['psa']['advanced_avoided'], 0)}), "
      f"{_n(hy['central']['deaths_avoided_5y'], 0)} deaths ({_ci(hy['psa']['deaths_avoided_5y'], 0)}) and "
      f"{_n(hy['central']['qalys_disc'], 0)} QALYs ({_ci(hy['psa']['qalys_disc'], 0)}) a year. "
      f"Deaths avoided were above zero in {h['psa_share_deaths_avoided_positive']:.0%} of PSA draws.")
    a("")
    a("Central values use every parameter at its central value and the shares pooled over all replications; the intervals are "
      "the 2.5th and 97.5th percentiles of the PSA, which draws every parameter from its range and one simulation replication.")
    a("")
    # ---- scenarios table per 1,000
    a("## Per 1,000 FIT-positive cases, by scenario")
    a("")
    a("Orchestrated minus baseline, same synthetic patients. Central value, then the 95% PSA interval.")
    a("")
    names = [s for s in SCENARIO_ORDER if s in sc]
    a("| Outcome | " + " | ".join(SCENARIO_LABEL[s] for s in names) + " |")
    a("|---|" + "---|" * len(names))
    for k, (lab, d) in LABELS.items():
        cells = [f"{_n(sc[s]['groups']['all']['central'][k], d)} ({_ci(sc[s]['groups']['all']['psa'][k], d)})" for s in names]
        a(f"| {lab} | " + " | ".join(cells) + " |")
    a("| No colonoscopy by day 270, baseline -> orchestrated | " + " | ".join(
        f"{1 - sc[s]['groups']['all']['shares']['baseline']['prompt']:.1%} -> {1 - sc[s]['groups']['all']['shares']['orchestrated']['prompt']:.1%}"
        for s in names) + " |")
    a("| No colonoscopy within 360 days, baseline -> orchestrated | " + " | ".join(
        f"{sc[s]['groups']['all']['shares']['baseline']['none']:.1%} -> {sc[s]['groups']['all']['shares']['orchestrated']['none']:.1%}"
        for s in names) + " |")
    a("")
    a("## Per year in Abu Dhabi")
    a("")
    a(f"Per 1,000 figures times {_n(P['positives_per_year_abu_dhabi']['value'], 0)} / 1,000 (business-case.md section 1.1). "
      "The volume itself is an estimate (8,216 to 59,724) and its range is not carried into these intervals.")
    a("")
    a("| Outcome a year | " + " | ".join(SCENARIO_LABEL[s] for s in names) + " |")
    a("|---|" + "---|" * len(names))
    for k in ("extra_colonoscopies", "extra_aa_removed", "advanced_avoided", "deaths_avoided_5y", "life_years_disc", "qalys_disc"):
        lab = LABELS[k][0]
        cells = [f"{_n(sc[s]['groups']['all']['per_year_abu_dhabi']['central'][k], 0)} ({_ci(sc[s]['groups']['all']['per_year_abu_dhabi']['psa'][k], 0)})" for s in names]
        a(f"| {lab} | " + " | ".join(cells) + " |")
    a("")
    a("**How to read the scenarios.** The literature-anchored scenario is the headline. The Abu Dhabi-shaped overlay starts from the "
      "30.5% local audit baseline with the same mechanism and reach; it is a sensitivity run, not a measured local result. The "
      "upper range is the mechanism ceiling, not a forecast. The capacity row puts the headline scenario through a shared "
      "colonoscopy calendar and a staff-minutes budget at base supply (results.md, Under finite capacity).")
    a("")
    # ---- stage distribution
    a("## Stage at diagnosis by delay group")
    a("")
    a("Central values. The prompt group is Corley 2017's colonoscopy within 9 months; the late groups apply Corley's adjusted odds ratios.")
    a("")
    a("| Delay group | Any CRC per 1,000 | Advanced (III-IV) per 1,000 | Advanced share of cancers |")
    a("|---|---|---|---|")
    for b, lab in (("prompt", "Colonoscopy within 270 days"), ("late", "Colonoscopy at 271-360 days"),
                   ("none", "No colonoscopy within 360 days (treated as beyond 12 months)")):
        r = mc["risk_per_1000"][b]
        a(f"| {lab} | {_n(r['any_crc'], 1)} | {_n(r['advanced'], 1)} | {r['advanced_share_of_cancers']:.1%} |")
    a("")
    hb, ho = h["arms_central"]["baseline"], h["arms_central"]["orchestrated"]
    a("Cancers per 1,000 FIT-positive cases by stage, headline scenario (central):")
    a("")
    a("| Arm | Early (I-II) | Stage III | Stage IV | All | Deaths within 5 years |")
    a("|---|---|---|---|---|---|")
    for lab, x in (("Baseline", hb), ("Orchestrated", ho)):
        a(f"| {lab} | {_n(x['early'], 2)} | {_n(x['stage_III'], 2)} | {_n(x['stage_IV'], 2)} | {_n(x['cancers'], 2)} | {_n(x['deaths_5y'], 2)} |")
    a("")
    a(f"5-year survival used (central): early stage {mc['surv5_early']:.1%}, advanced stage {mc['surv5_advanced']:.1%} "
      f"(stage III and IV weighted). One death avoided gains {_n(mc['life_years_per_death_avoided'], 1)} life-years "
      f"({_n(mc['life_years_per_death_avoided_disc'], 1)} discounted).")
    a("")
    # ---- cost per QALY
    a("## Cost per QALY (illustrative)")
    a("")
    a("Payer view, first-year treatment costs only, business-case inputs. **Illustrative: the fee is not an offer, the colonoscopy "
      "price is an aggregator estimate, the treatment costs are from one Saudi hospital, and the QALYs rest on the assumptions above.**")
    a("")
    a("| Per 1,000 positives, central | " + " | ".join(SCENARIO_LABEL[s] for s in names) + " |")
    a("|---|" + "---|" * len(names))
    for k, lab in (("programme_cost_aed", "Fee (AED 90 per case)"), ("colonoscopy_cost_aed", "Extra colonoscopies"),
                   ("treatment_cost_avoided_aed", "Treatment cost avoided"), ("net_cost_aed", "Net cost"),
                   ("cost_per_qaly_aed", "Net cost per QALY")):
        a(f"| {lab} | " + " | ".join(_aed(sc[s]['groups']['all']['central'][k]) for s in names) + " |")
    a("| Cost per QALY, PSA median (2.5th to 97.5th percentile) | " + " | ".join(
        (f"{_aed(sc[s]['groups']['all']['psa_cost_per_qaly'].get('median'))} ({_aed(sc[s]['groups']['all']['psa_cost_per_qaly'].get('p025'))} to "
         f"{_aed(sc[s]['groups']['all']['psa_cost_per_qaly'].get('p975'))})") for s in names) + " |")
    a("| Share of PSA draws at or below 1x UAE GDP per capita a QALY | " + " | ".join(
        f"{sc[s]['groups']['all']['psa_cost_per_qaly']['share_below_1x_gdp_per_capita']:.0%}" for s in names) + " |")
    a("")
    a(f"For orientation only: UAE GDP per capita was about {_aed(mc['gdp_per_capita_aed'])} in 2024 (World Bank). The UAE publishes "
      "no cost-effectiveness threshold, and GDP-based thresholds are contested, so this is a scale marker, not a verdict. The extra "
      "colonoscopies are most of the cost; they are the point of the programme, not waste.")
    a("")
    # ---- equity
    a("## Equity: who gains")
    a("")
    a("Headline scenario, per 1,000 FIT-positive cases in each group (central, then the 95% PSA interval, which here also carries "
      "the smaller groups' simulation noise). Languages and channels are the simulator's synthetic shares (assumed, "
      "simulator/params.yaml); contact = the number or address on file is wrong (`wrong_contact`); reach = inside or outside "
      "`orchestration.programme_reach`. No nationality, age or sex data exist in the simulator, so none is reported.")
    a("")
    a("| Group | Cases (all replications) | No colonoscopy within 360 d, baseline -> orchestrated | CRC deaths within 5 years, baseline -> orchestrated | Advanced cancers avoided | Deaths avoided within 5 years | QALYs |")
    a("|---|---|---|---|---|---|---|")
    hg = sc[HEADLINE]["groups"]
    for dim, vals in GROUP_DIMS:
        for v in vals:
            g = hg.get(f"{dim}:{v}")
            if not g:
                continue
            a(f"| {dim} = {v} | {g['n_cases']['baseline']:,} | {g['shares']['baseline']['none']:.1%} -> {g['shares']['orchestrated']['none']:.1%} | "
              f"{_n(g['arms_central']['baseline']['deaths_5y'], 2)} -> {_n(g['arms_central']['orchestrated']['deaths_5y'], 2)} | "
              f"{_n(g['central']['advanced_avoided'], 2)} ({_ci(g['psa']['advanced_avoided'], 2)}) | "
              f"{_n(g['central']['deaths_avoided_5y'], 2)} ({_ci(g['psa']['deaths_avoided_5y'], 2)}) | "
              f"{_n(g['central']['qalys_disc'], 0)} ({_ci(g['psa']['qalys_disc'], 0)}) |")
    a("")
    a(equity_reading(res))
    a("")
    # ---- sensitivity
    a("## Sensitivity: what drives deaths avoided")
    a("")
    a("Spearman rank correlation between each PSA input and deaths avoided within 5 years (headline scenario, all cases). "
      "Larger absolute values matter more.")
    a("")
    drv = h["drivers_deaths_avoided"]
    a("| Input | Rank correlation |")
    a("|---|---|")
    for k, v in sorted(drv.items(), key=lambda kv: -abs(kv[1] or 0)):
        a(f"| `{k}` | {_n(v, 2)} |")
    a("")
    # ---- parameters
    a("## Parameters")
    a("")
    a("Full citations, access dates and extracted values are in `simulator/params.outcomes.yaml` and docs/evidence.md section 14.")
    a("")
    a("| Parameter | Central | PSA range | Status | Source |")
    a("|---|---|---|---|---|")
    for n, spec in P.items():
        val = spec["value"]
        rng = "fixed" if spec.get("dist") in (None, "fixed") else f"{spec['dist']} {spec.get('args')}"
        src = (spec.get("source") or "").split(". ")[0]
        a(f"| `{n}` | {val} | {rng} | {spec.get('status')} | {src} |")
    a("")
    a(LIMITATIONS)
    return "\n".join(L) + "\n"


def equity_reading(res: dict) -> str:
    hg = res["scenarios"][HEADLINE]["groups"]

    def dv(g):
        return hg[g]["central"]["deaths_avoided_5y"] if g in hg else None
    lines = ["**Reading.**"]
    for dim, vals in GROUP_DIMS:
        got = [(v, dv(f"{dim}:{v}")) for v in vals if dv(f"{dim}:{v}") is not None]
        if len(got) < 2:
            continue
        lo, hi = min(got, key=lambda t: t[1]), max(got, key=lambda t: t[1])
        left = [(v, hg[f"{dim}:{v}"]["shares"]["orchestrated"]["none"]) for v, _ in got]
        best, worst = min(left, key=lambda t: t[1]), max(left, key=lambda t: t[1])
        lines.append(f"By {dim}, deaths avoided per 1,000 range from {lo[1]:.2f} ({lo[0]}) to {hi[1]:.2f} ({hi[0]}); after "
                     f"orchestration {worst[1]:.1%} of the {worst[0]} group still has no colonoscopy within 360 days against "
                     f"{best[1]:.1%} of the {best[0]} group.")
    lines.append(
        "Gains per 1,000 follow the completion gains, and a group that starts worse off has more to gain, so equal gains are "
        "not the test: the residual gap after orchestration is. What the system does about "
        "it: `make evaluate` reports the completion and closure gaps by language and channel (results.md, Fairness), and in the "
        "running service the fairness watch (api/app/agency/agents.py, `FairnessWatch`) raises a supervisor task when a language "
        "group's overdue rate, or a language or channel group's model-abstention rate, is more than 10 points above the overall "
        "rate. The watch checks overdue rates for Arabic and English only, not the 'other' language group, and does not look at "
        "contact quality; both are gaps worth closing. Cases outside the programme's reach gain nothing by construction "
        "(they are handled as today), so reach, not language, is the largest equity lever in this model.")
    return " ".join(lines)


LIMITATIONS = """## Limitations, in plain words

1. **Synthetic, not observed.** The colonoscopy timing comes from the simulator. Its 180-day completion ratio is calibrated to published navigation trials. Its timing between 180 and 360 days, which decides the delay groups here, is not validated (validation.md).
2. **The delay effect is observational.** Corley 2017 is a Kaiser Permanente cohort in California (2010 to 2014). People who wait longer may differ in other ways, so the odds ratios may overstate the causal harm of delay. The adjusted odds ratios are applied to the prompt group's risk.
3. **Non-completers are treated as completing after 12 months.** People who never have a colonoscopy and are diagnosed only when symptoms appear probably fare worse, so this choice tends to understate the gain.
4. **Survival is not from the UAE.** No UAE stage-specific survival was found. The central values are from a Saudi registry (2009 to 2017), and the upper ends are from US SEER. Five-year survival is used as a proxy for cure, so later deaths and late recurrences are ignored.
5. **Life-years are simple.** A death avoided gains the WHO 2023 UAE life expectancy at diagnosis, minus an assumed 2 years. There is no extra mortality for survivors, no quality-of-life loss during treatment, and no harm from colonoscopy itself (perforation or bleeding are not modelled). The first two tend to overstate QALYs, and the treatment-quality omission tends to understate them.
6. **Costs are illustrative.** The fee is an illustration, not an offer. The colonoscopy price is an aggregator range, and the treatment costs are first-year costs from one Saudi hospital. The extra colonoscopies are counted as within 360 days, and the colonoscopies non-completers would eventually have are not netted off.
7. **Equity groups are synthetic.** The language and channel mix is assumed. No nationality, age or sex variable exists in the simulator, so none is reported, and none is invented.
8. **No clinician has reviewed this model.** It needs review by a gastroenterologist and a health economist before any number is quoted outside this repository.
"""


def write(res: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j, mfile = out_dir / "outcomes.json", out_dir / "outcomes.md"
    j.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    mfile.write_text(render_md(res))
    return [j, mfile]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--days", type=int, default=360)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--params", default=str(OUTCOME_PARAMS))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "results"))
    ap.add_argument("--check", action="store_true", help="rerun and compare with the committed files")
    args = ap.parse_args(argv)
    cfg = load_outcome_params(Path(args.params))
    out_dir = Path(args.out_dir)
    if args.check:
        res = build(args.reps, args.cases, args.days, args.seed, args.jobs, cfg)
        with tempfile.TemporaryDirectory() as tmp:
            fresh = write(res, Path(tmp))
            bad = [f.name for f in fresh if not (out_dir / f.name).exists() or (out_dir / f.name).read_bytes() != f.read_bytes()]
        if bad:
            print(f"evaluate-outcomes-check: FAIL, {', '.join(bad)} differ from a fresh run "
                  "(run `make evaluate-outcomes` after any engine, parameter or seed change)")
            return 1
        print("evaluate-outcomes-check: PASS, outcomes.json and outcomes.md are byte-identical to a fresh run")
        return 0
    res = build(args.reps, args.cases, args.days, args.seed, args.jobs, cfg)
    paths = write(res, out_dir)
    print("wrote " + ", ".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
