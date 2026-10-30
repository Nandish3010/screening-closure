#!/usr/bin/env python3
"""Cost-per-case model for the 90-day pilot and for steady-state volumes.

Usage:  python3 tools/pilot/cost_model.py [--commercial | --pricing [--write]] [--inputs tools/pilot/inputs.yaml] [--results docs/results/results.json]

Deterministic: no randomness, no clock, no network, standard library only. Every cost number it prints is
arithmetic on tools/pilot/inputs.yaml, and every input there is an assumption (see that file). The output
is illustrative; it is not a quote or a forecast. The one exception is the value table, which also reads the
simulated shares of cases with no colonoscopy by day 270 from docs/results/results.json (synthetic). The
wage-weighted staff-time section also reads the simulated minutes per case by role from the same file, and
the three wage ratios (nurse, clinician, specialist; docs/evidence-wages.md section 4) from the inputs file.
"""
import argparse
import json
import math
import sys
from pathlib import Path

DEFAULT_INPUTS = Path(__file__).with_name("inputs.yaml")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = REPO_ROOT / "docs" / "results" / "results.json"
DEFAULT_OUTCOMES = REPO_ROOT / "docs" / "results" / "outcomes.json"  # M7-3 health outcomes (optional)
ALLOWED_STATUS = ("assumed", "sourced-adjacent", "sourced")
LEGACY_SECTIONS = ("inputs", "pilot", "shadow_pilot", "value")
COMMERCIAL_SECTIONS = ("market", "outcome", "capacity", "benchmark")
PRICING_SECTIONS = ("azure", "pathways", "pricing", "operator", "buyer")
BUSINESS_CASE = REPO_ROOT / "docs" / "commercial" / "business-case.md"
PRICING_BEGIN = "<!-- BEGIN GENERATED: python3 tools/pilot/cost_model.py --pricing --write (do not edit by hand) -->"
PRICING_END = "<!-- END GENERATED: pricing -->"
HOURS_PER_YEAR_FALLBACK = 1700
WAGE_RATIO_INPUTS = ("wage_ratio_nurse", "wage_ratio_clinician", "wage_ratio_specialist")
# Roles in results.json effort.scenarios.<scenario>.<all|in_reach>.arms.<arm> as "minutes_per_case.<role>".
EFFORT_ROLES = ("coordinator", "owner_clinician", "specialist")


# --- minimal YAML-subset reader (nested mappings, scalars, inline lists, comments) -------------------------

def _scalar(text):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def parse_yaml_subset(text):
    root = {}
    stack = [(-1, root)]
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, rest = line.strip().partition(":")
        if not sep:
            raise ValueError(f"line {number}: expected 'key: value'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if rest.strip() == "":
            child = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(rest)
    return root


def load_inputs(path):
    data = parse_yaml_subset(Path(path).read_text(encoding="utf-8"))
    flat = {}
    for section in LEGACY_SECTIONS + COMMERCIAL_SECTIONS + PRICING_SECTIONS:
        for name, spec in data.get(section, {}).items():
            for field in ("value", "unit", "status", "basis"):
                if field not in spec:
                    raise ValueError(f"input '{name}' is missing '{field}'")
            if spec["status"] not in ALLOWED_STATUS:
                raise ValueError(f"input '{name}' has status '{spec['status']}'; use one of {ALLOWED_STATUS}")
            spec["section"] = section
            flat[name] = spec
    for name in WAGE_RATIO_INPUTS:
        if name not in flat:
            raise ValueError(f"input '{name}' is missing (wage ratios are read from the inputs file)")
    for name in WAGE_RATIO_INPUTS:
        for field in ("low", "high"):
            if field not in flat[name]:
                raise ValueError(f"input '{name}' is missing '{field}' (the sensitivity range)")
    return data, flat


# --- model --------------------------------------------------------------------------------------------------

def scenario(members, v):
    cases = members * v["positivity_rate"]
    providers = max(1, math.ceil(members / v["members_per_provider"]))
    hosting = v["hosting_fixed_aed_per_month"] * 12 + v["hosting_variable_aed_per_1000_cases_per_year"] * cases / 1000
    licences = v["third_party_licences_aed_per_year"]
    fte = v["support_fte_base"] + v["support_fte_per_50k_members"] * members / 50000
    support = fte * v["support_fte_loaded_cost_aed_per_year"]
    integration = providers * v["integration_one_off_aed_per_provider"] / v["amortisation_years"]
    total = hosting + licences + support + integration
    saved_hours = cases * (v["coordinator_minutes_per_case_manual"] - v["coordinator_minutes_per_case_with_engine"]) / 60
    saved_value = saved_hours * v["coordinator_cost_aed_per_hour"]
    fee_a = members * v["price_per_screened_member_per_year_aed"]
    fee_b = cases * v["price_per_actionable_case_aed"]
    return {
        "members": members, "cases": cases, "providers": providers, "hosting": hosting, "licences": licences,
        "support_fte": fte, "support": support, "integration": integration, "total": total,
        "cost_per_case": total / cases, "cost_per_member": total / members,
        "saved_hours": saved_hours, "saved_value": saved_value, "fee_a": fee_a, "fee_b": fee_b,
        "net_a": saved_value - fee_a, "net_b": saved_value - fee_b,
        "margin_a": fee_a - total, "margin_b": fee_b - total,
    }


def pilot(v):
    n = v["pilot_providers"]
    window, tail = v["pilot_days"], v["followup_tail_days"]
    fits = n * v["pilot_fit_screened_per_provider_per_year"] * window / 365
    cases = fits * v["positivity_rate"]
    run_days = window + tail
    hosting = v["hosting_fixed_aed_per_month"] * 12 * run_days / 365 + v["hosting_variable_aed_per_1000_cases_per_year"] * cases / 1000
    licences = v["third_party_licences_aed_per_year"] * run_days / 365
    support = v["support_fte_loaded_cost_aed_per_year"] * (v["pilot_support_fte"] * window + v["tail_support_fte"] * tail) / 365
    integration = n * v["integration_one_off_aed_per_provider"]
    governance = v["pilot_governance_review_one_off_aed"]
    total = hosting + licences + support + integration + governance
    return {"fits": fits, "cases": cases, "run_days": run_days, "hosting": hosting, "licences": licences,
            "support": support, "integration": integration, "governance": governance, "total": total,
            "cost_per_case": total / cases}


def shadow_pilot(v):
    """Shadow-only run fed by a periodic flat-file or HL7 v2 export: nothing is sent, nothing is assigned."""
    n = v["pilot_providers"]
    days = v["shadow_days"]
    fits = n * v["pilot_fit_screened_per_provider_per_year"] * days / 365
    cases = fits * v["positivity_rate"]
    share = v["shadow_hosting_share_of_full"]
    hosting = share * (v["hosting_fixed_aed_per_month"] * 12 * days / 365
                       + v["hosting_variable_aed_per_1000_cases_per_year"] * cases / 1000)
    licences = v["third_party_licences_aed_per_year"] * v["shadow_licences_share_of_full"] * days / 365
    support = v["support_fte_loaded_cost_aed_per_year"] * v["shadow_support_fte"] * days / 365
    integration = n * v["shadow_integration_one_off_aed_per_provider"]
    governance = v["shadow_governance_review_one_off_aed"]
    total = hosting + licences + support + integration + governance
    return {"fits": fits, "cases": cases, "days": days, "hosting": hosting, "licences": licences,
            "support": support, "integration": integration, "governance": governance, "total": total,
            "cost_per_case": total / cases}


def odds_ratio_risk(p, odds_ratio):
    """Risk after applying an odds ratio to a baseline risk p."""
    odds = p / (1 - p) * odds_ratio
    return odds / (1 + odds)


def load_delay_shares(path):
    """Simulated share of cases with no colonoscopy by day 270 (delay_harm_share mean), from results.json."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    scen = data["scenarios"]
    out = {"baseline": scen["literature_anchored"]["arms"]["baseline"]["delay_harm_share"]["mean"],
           "literature_anchored": scen["literature_anchored"]["arms"]["orchestrated"]["delay_harm_share"]["mean"]}
    if "upper_range" in scen:
        out["upper_range"] = scen["upper_range"]["arms"]["orchestrated"]["delay_harm_share"]["mean"]
    return out


def load_effort_minutes(path):
    """Mean human minutes per case by role, from results.json effort (synthetic).

    Returns a list of (label, minutes, baseline_label) rows in print order; minutes is a dict
    {coordinator, owner_clinician, specialist}. Each orchestrated row is compared with the baseline
    of the same scenario and the same cases (all cases, or the cases the programme reaches).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    scen = data["effort"]["scenarios"]

    def minutes(scenario_key, subset, arm):
        block = scen[scenario_key][subset]["arms"][arm]
        return {role: block["minutes_per_case." + role]["mean"] for role in EFFORT_ROLES}

    rows = [
        ("baseline, all cases", minutes("literature_anchored", "all", "baseline"), None),
        ("literature anchored, programme level", minutes("literature_anchored", "all", "orchestrated"),
         "baseline, all cases"),
        ("baseline, cases in reach", minutes("literature_anchored", "in_reach", "baseline"), None),
        ("literature anchored, in reach", minutes("literature_anchored", "in_reach", "orchestrated"),
         "baseline, cases in reach"),
    ]
    if "upper_range" in scen:
        rows.append(("upper range (every case reached)", minutes("upper_range", "all", "orchestrated"),
                     "baseline, upper-range run"))
        rows.insert(4, ("baseline, upper-range run", minutes("upper_range", "all", "baseline"), None))
    return rows


def weighted_minutes(minutes, clinician_ratio, specialist_ratio):
    """Coordinator-equivalent minutes: coordinator + clinician ratio x owner clinician + specialist ratio x specialist."""
    return (minutes["coordinator"] + clinician_ratio * minutes["owner_clinician"]
            + specialist_ratio * minutes["specialist"])


def value_case(cases, shares, scenario_key, v, case_label):
    """Cancers potentially avoided for `cases` positive FIT cases (illustrative, synthetic shares x published ORs)."""
    suffix = "10_to_12_months" if case_label == "10-12 month ORs" else "over_12_months"
    p_any, p_adv = v["crc_risk_on_time"], v["advanced_stage_risk_on_time"]
    excess_any = odds_ratio_risk(p_any, v["or_any_crc_" + suffix]) - p_any
    excess_adv = odds_ratio_risk(p_adv, v["or_advanced_stage_" + suffix]) - p_adv
    moved = (shares["baseline"] - shares[scenario_key]) * cases
    return {"moved": moved, "earlier": moved * excess_any, "late_stage": moved * excess_adv}


# --- commercial extension (ticket E17): market, outcome value, capacity ----------------------------------------

def range_of(spec, name):
    """(low, central, high) of an input; an input with no low/high is a point."""
    s = spec[name]
    return s.get("low", s["value"]), s["value"], s.get("high", s["value"])


def market_positives(population, participation, positivity):
    """Expected FIT-positive cases a year: eligible people x share with a FIT result x share positive."""
    return population * participation * positivity


def market_table(spec):
    """Rows of (label, eligible, FIT results, positives) for low / central / high, Abu Dhabi, UAE and GCC."""
    part_lo, part_c, part_hi = range_of(spec, "fit_participation_annual")
    pos_lo, pos_c, pos_hi = range_of(spec, "fit_positivity_market")
    g_part = range_of(spec, "gcc_fit_participation_annual")
    g_share = range_of(spec, "gcc_eligible_share_of_population")
    v = {k: s["value"] for k, s in spec.items()}
    small_pop = sum(v[k] for k in ("qatar_population_total", "kuwait_population_total",
                                    "oman_population_total", "bahrain_population_total"))
    cases = (("low", 0, part_lo, pos_lo, g_part[0], g_share[0]),
             ("central", 1, part_c, pos_c, g_part[1], g_share[1]),
             ("high", 2, part_hi, pos_hi, g_part[2], g_share[2]))
    out = []
    for label, _, part, pos, gpart, gshare in cases:
        ad = v["abu_dhabi_population_40_to_75"]
        uae = v["uae_population_40_to_75"]
        saudi = v["saudi_population_45_to_75"]
        small = small_pop * gshare
        out.append({
            "case": label, "participation": part, "positivity": pos,
            "ad_fits": ad * part, "ad_positives": market_positives(ad, part, pos),
            "uae_fits": uae * part, "uae_positives": market_positives(uae, part, pos),
            "saudi_fits": saudi * gpart, "saudi_positives": market_positives(saudi, gpart, pos),
            "small_eligible": small, "small_fits": small * gpart,
            "small_positives": market_positives(small, gpart, pos),
        })
    return out


def stage_cost_gap_aed(spec, basis):
    """Cost gap between stage I and stage IV per patient, in AED, from the Saudi (USD, 2020) or Malaysian (MYR, 2012) study."""
    v = {k: s["value"] for k, s in spec.items()}
    peg = v["usd_aed_peg"]
    if basis == "saudi":
        return (v["crc_cost_stage4_usd_saudi"] - v["crc_cost_stage1_usd_saudi"]) * peg
    if basis == "malaysia":
        return (v["crc_cost_stage4_myr_malaysia"] - v["crc_cost_stage1_myr_malaysia"]) / v["myr_per_usd_malaysia"] * peg
    raise ValueError(f"unknown cost basis {basis}")


def outcome_value(cases, shares, scenario_key, spec, or_label, cost_basis, pair_factor=None):
    """Treatment cost avoided for `cases` positive FITs (illustrative; synthetic shares x published odds ratios x cost gap).

    Cases moved from 'no colonoscopy by day 270' to 'colonoscopy by day 270' are assumed to carry the excess
    advanced-stage risk of a late colonoscopy (Corley 2017 odds ratio applied to the derived on-time risk). Each
    advanced-stage case avoided saves the stage I-to-IV cost gap times the pair factor.
    """
    v = {k: s["value"] for k, s in spec.items()}
    suffix = "10_to_12_months" if or_label == "10-12 month ORs" else "over_12_months"
    p_adv = v["advanced_stage_risk_on_time_derived"]
    excess = odds_ratio_risk(p_adv, v["or_advanced_stage_" + suffix]) - p_adv
    moved = (shares["baseline"] - shares[scenario_key]) * cases
    avoided = moved * excess
    factor = v["stage_pair_factor"] if pair_factor is None else pair_factor
    cost = avoided * stage_cost_gap_aed(spec, cost_basis) * factor
    return {"moved": moved, "late_stage_avoided": avoided, "cost_avoided": cost,
            "cost_per_case": cost / cases}


def capacity_fte(cases, rows, spec):
    """FTEs freed or consumed by role for `cases` positive FITs a year, from results.json effort minutes (synthetic).

    Returns {row label: {coordinator, owner_clinician, specialist, all_roles}} in FTE; positive = freed,
    negative = more time needed than at baseline. Each orchestrated row is compared with the baseline of the same cases.
    """
    hours = spec["productive_hours_per_fte_year"]["value"] if "productive_hours_per_fte_year" in spec else HOURS_PER_YEAR_FALLBACK
    by_label = {label: m for label, m, _ in rows}
    out = {}
    for label, m, base in rows:
        if base is None:
            continue
        b = by_label[base]
        fte = {role: cases * (b[role] - m[role]) / 60.0 / hours for role in EFFORT_ROLES}
        fte["all_roles"] = sum(fte.values())
        out[label] = fte
    return out


# --- output -------------------------------------------------------------------------------------------------

def money(x):
    return f"{x:,.0f}"


def table(headers, rows, left=(0,)):
    widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    def fmt(r):
        return "  ".join(str(c).ljust(widths[i]) if i in left else str(c).rjust(widths[i]) for i, c in enumerate(r)).rstrip()
    return "\n".join([fmt(headers), "  ".join("-" * w for w in widths)] + [fmt(r) for r in rows])

def print_commercial(spec, shares, effort_rows, results_name):
    v = {k: s["value"] for k, s in spec.items()}
    com = {k: s for k, s in spec.items() if s["section"] in COMMERCIAL_SECTIONS}
    print("COMMERCIAL CASE EXTENSION (ticket E17; illustrative, not a quote, forecast or clinical claim)")
    print("Reads the market, outcome, capacity and benchmark sections of the inputs file, and the simulated 270-day shares and")
    print(f"minutes by role from {results_name} (synthetic). Statuses are graded per input: assumed, sourced-adjacent, sourced.\n")
    print("COMMERCIAL INPUTS")
    print(table(["input", "value", "low", "high", "status", "unit"],
                [[k, s["value"], s.get("low", "-"), s.get("high", "-"), s["status"], s["unit"]] for k, s in com.items()],
                left=(0, 4, 5)))

    print("\nMARKET: expected FIT results and FIT-positive cases a year (eligible x participation x positivity)")
    print("Abu Dhabi and UAE use ages 40-75 and the same participation and positivity; Saudi uses ages 45-75 and the GCC participation;")
    print("Qatar, Kuwait, Oman and Bahrain use total residents x the assumed eligible share and the GCC participation.")
    rows = market_table(spec)
    head = ["per year"] + [r["case"] for r in rows]
    def mrow(label, key, fmt=money):
        return [label] + [fmt(r[key]) for r in rows]
    print(table(head, [
        mrow("participation (share with a FIT)", "participation", lambda x: f"{x:.0%}"),
        mrow("positivity", "positivity", lambda x: f"{x:.1%}"),
        mrow("Abu Dhabi: FIT results", "ad_fits"),
        mrow("Abu Dhabi: FIT-positive cases", "ad_positives"),
        mrow("UAE: FIT results", "uae_fits"),
        mrow("UAE: FIT-positive cases", "uae_positives"),
        mrow("Saudi Arabia: FIT results", "saudi_fits"),
        mrow("Saudi Arabia: FIT-positive cases", "saudi_positives"),
        mrow("Qatar+Kuwait+Oman+Bahrain: eligible", "small_eligible"),
        mrow("Qatar+Kuwait+Oman+Bahrain: FIT-positive cases", "small_positives"),
    ]))
    price_b, price_a = v["price_per_actionable_case_aed"], v["price_per_screened_member_per_year_aed"]
    central = rows[1]
    print(f"Revenue pool at the illustrative fees, central case: option B (AED {price_b:g} per positive case) Abu Dhabi "
          f"{money(central['ad_positives'] * price_b)}, UAE {money(central['uae_positives'] * price_b)}; option A "
          f"(AED {price_a:g} per member screened) Abu Dhabi {money(central['ad_fits'] * price_a)}, UAE {money(central['uae_fits'] * price_a)}.")

    print("\nOUTCOME VALUE: treatment cost avoided per 1,000 positive FITs (ILLUSTRATIVE, SYNTHETIC SHARES x PUBLISHED ORs x COST GAP)")
    print(f"Shares with no colonoscopy by day 270 (synthetic): baseline {shares['baseline']:.1%}, literature-anchored "
          f"{shares['literature_anchored']:.1%}" + (f", upper-range {shares['upper_range']:.1%}." if "upper_range" in shares else "."))
    p_adv = v["advanced_stage_risk_on_time_derived"]
    gap_s, gap_m = stage_cost_gap_aed(spec, "saudi"), stage_cost_gap_aed(spec, "malaysia")
    pf_lo, pf_c, pf_hi = range_of(spec, "stage_pair_factor")
    print(f"On-time advanced-stage risk {p_adv:.4f} (derived from Corley 2017 absolute risks). Stage I to IV cost gap per patient, first year:")
    print(f"  Saudi study (USD, 2020 prices x {v['usd_aed_peg']:g}): AED {money(gap_s)}; Malaysian study (MYR, 2012, not inflated): AED {money(gap_m)}.")
    print(f"Pair factor (share of that gap that one advanced-to-early shift saves; assumed): {pf_c:g}, sensitivity {pf_lo:g} to {pf_hi:g}.")
    cells = []
    for scen_key, scen_label in (("literature_anchored", "literature-anchored"), ("upper_range", "upper-range")):
        if scen_key not in shares:
            continue
        for or_label in ("10-12 month ORs", ">12 month ORs"):
            lo = outcome_value(1000, shares, scen_key, spec, or_label, "malaysia")
            hi = outcome_value(1000, shares, scen_key, spec, or_label, "saudi")
            cells.append((scen_label, or_label, lo, hi))
    print(table(["per 1,000 positive FITs", "moved to colonoscopy", "late-stage avoided", "AED avoided, Malaysian gap",
                 "AED avoided, Saudi gap", "AED per case, Saudi gap", f"avoided / option B AED {price_b:g}"],
                [[f"{a}, {b}", f"{lo['moved']:,.1f}", f"{lo['late_stage_avoided']:,.2f}", money(lo["cost_avoided"]),
                  money(hi["cost_avoided"]), f"{hi['cost_per_case']:,.0f}", f"{hi['cost_per_case'] / price_b:.2f}x"]
                 for a, b, lo, hi in cells]))
    base = outcome_value(1000, shares, "literature_anchored", spec, "10-12 month ORs", "saudi")
    sens = [outcome_value(1000, shares, "literature_anchored", spec, "10-12 month ORs", "saudi", pair_factor=f)
            for f in (pf_lo, pf_c, pf_hi)]
    print("Sensitivity of the headline row (literature-anchored, 10-12 month ORs, Saudi gap) to the pair factor, AED avoided per 1,000: "
          + ", ".join(f"{f:g}: {money(r['cost_avoided'])}" for f, r in zip((pf_lo, pf_c, pf_hi), sens)) + ".")
    breakeven = price_b / base["cost_per_case"] if base["cost_per_case"] else float("inf")
    print(f"Break-even for the headline row: the option B fee equals the cost avoided when it is AED {base['cost_per_case']:,.0f} per case "
          f"(the illustrative fee is {breakeven:.2f} times that).")
    print("Direct treatment cost in the first year only; excludes survival, deaths, productivity and later years. Cases that never")
    print("complete are treated as if they completed late. Observational ORs, confounding by indication.")

    print("\nCAPACITY VALUE: coordinator and clinician FTE freed per 10,000 positive FITs a year (synthetic minutes; capacity, not cash)")
    hrs_lo, hrs_c, hrs_hi = range_of(spec, "productive_hours_per_fte_year")
    print(f"Minutes by role from {results_name} effort (keys effort.scenarios.<scenario>.<all|in_reach>.arms.<arm>.minutes_per_case.<role>.mean);")
    print(f"FTE = cases x minutes saved / 60 / productive hours ({hrs_c:g} per FTE a year, assumed; range {hrs_lo:g} to {hrs_hi:g}). Positive = freed.")
    fte = capacity_fte(10000, effort_rows, spec)
    fte_lo = capacity_fte(10000, effort_rows, {**spec, "productive_hours_per_fte_year": {"value": hrs_hi}})
    fte_hi = capacity_fte(10000, effort_rows, {**spec, "productive_hours_per_fte_year": {"value": hrs_lo}})
    by_label = {label: m for label, m, _ in effort_rows}
    cap_rows = []
    for label, f in fte.items():
        cap_rows.append([label, f"{f['coordinator']:+.2f}", f"{f['owner_clinician']:+.2f}", f"{f['specialist']:+.2f}",
                         f"{f['all_roles']:+.2f}", f"{fte_lo[label]['coordinator']:+.2f} to {fte_hi[label]['coordinator']:+.2f}"])
    print(table(["scenario (vs baseline of same cases)", "coordinator FTE", "owner clinician FTE", "specialist FTE", "all roles FTE",
                 "coordinator FTE range (hours)"], cap_rows, left=(0,)))
    base_all = by_label["baseline, all cases"]
    print(f"Baseline workload for scale: {10000 * base_all['coordinator'] / 60 / hrs_c:.1f} coordinator FTE, "
          f"{10000 * base_all['owner_clinician'] / 60 / hrs_c:.1f} owner-clinician FTE and "
          f"{10000 * base_all['specialist'] / 60 / hrs_c:.1f} specialist FTE for 10,000 positives.")
    print("Coordinator time is freed; owner-clinician and specialist time rises, so the all-roles figure is much smaller than the")
    print("coordinator figure and clinician time is the scarcer resource. No queueing or shift pattern is modelled.")

    print("\nPRICE BENCHMARKS (human patient navigation, for positioning the per-case price)")
    peg = v["usd_aed_peg"]
    nav_lo, nav_c, nav_hi = range_of(spec, "navigation_cost_per_person_navigated_usd")
    add_lo, add_c, add_hi = range_of(spec, "navigation_cost_per_additional_person_usd")
    print(table(["benchmark (2022 USD x peg)", "low", "median", "high"], [
        ["navigation cost per person navigated, AED", money(nav_lo * peg), money(nav_c * peg), money(nav_hi * peg)],
        ["navigation cost per additional person screened, AED", money(add_lo * peg), money(add_c * peg), money(add_hi * peg)],
        [f"option B fee AED {price_b:g} as a share of the median per person navigated", "-", f"{price_b / (nav_c * peg):.1%}", "-"],
    ]))
    print("These are US-led human-navigator programmes for screening, not positive-FIT software contracts; they bound what a buyer")
    print("already pays per navigated person, they are not a price list for this product.")


# --- pricing and unit economics (ticket M7-4): Azure TCO, price points, break-even, payback, sensitivity ----------------

def load_programme_shares(path):
    """Closed share and silent-loss rate by arm (literature-anchored, programme level) from results.json (synthetic)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    arms = data["scenarios"]["literature_anchored"]["arms"]
    return {arm: {"closed_share": arms[arm]["closed_share"]["mean"],
                  "silent_loss_rate": arms[arm]["silent_loss_rate"]["mean"]} for arm in ("baseline", "orchestrated")}


def azure_lines(v):
    """Monthly production hosting on Azure UAE North at list price: [(item, sizing, USD a month)].

    Unit prices are the sourced inputs in the azure section; quantities are the deploy/bicep defaults or assumptions.
    """
    sec = v["hours_per_month"] * 3600
    vcpu, mem = v["aca_vcpu_usd_per_second"], v["aca_memory_usd_per_gib_second"]
    pg_hour = v["pg_vcore_usd_per_hour"] * v["hours_per_month"]
    ha = v["pg_ha_multiplier"]

    def aca(cpu, gib):
        return cpu * sec * vcpu + gib * sec * mem

    return [
        ("Container Apps: api, scheduler, watchdog",
         f"{v['aca_always_on_vcpu']:g} vCPU, {v['aca_always_on_memory_gib']:g} GiB always on",
         aca(v["aca_always_on_vcpu"], v["aca_always_on_memory_gib"])),
        ("Container Apps: FHIR server (HAPI)", f"{v['fhir_vcpu']:g} vCPU, {v['fhir_memory_gib']:g} GiB",
         aca(v["fhir_vcpu"], v["fhir_memory_gib"])),
        ("Container Apps: collector, Prometheus, Grafana",
         f"{v['observability_vcpu']:g} vCPU, {v['observability_memory_gib']:g} GiB",
         aca(v["observability_vcpu"], v["observability_memory_gib"])),
        ("Container Apps environment", f"{v['hours_per_month']:g} h", v["aca_environment_usd_per_hour"] * v["hours_per_month"]),
        ("PostgreSQL: case database compute", f"{v['pg_vcores']:g} vCores x {ha:g} (zone-redundant HA)",
         v["pg_vcores"] * pg_hour * ha),
        ("PostgreSQL: case database storage", f"{v['pg_storage_gb']:g} GB x {ha:g}",
         v["pg_storage_gb"] * v["pg_storage_usd_per_gb_month"] * ha),
        ("PostgreSQL: backup above the included amount", f"{v['pg_backup_billable_gb']:g} GB, LRS, in country",
         v["pg_backup_billable_gb"] * v["pg_backup_usd_per_gb_month"]),
        ("PostgreSQL: FHIR database compute", f"{v['fhir_pg_vcores']:g} vCores x {ha:g}", v["fhir_pg_vcores"] * pg_hour * ha),
        ("PostgreSQL: FHIR database storage", f"{v['fhir_pg_storage_gb']:g} GB x {ha:g}",
         v["fhir_pg_storage_gb"] * v["pg_storage_usd_per_gb_month"] * ha),
        ("Log Analytics: ingestion and retention",
         f"{v['log_gb_per_month']:g} GB a month, {v['log_retention_billable_months']:g} months billed retention",
         v["log_gb_per_month"] * (v["log_ingest_usd_per_gb"]
                                  + v["log_retention_billable_months"] * v["log_retention_usd_per_gb_month"])),
        ("Key Vault: operations", f"{v['kv_operations_per_month']:,.0f} a month",
         v["kv_operations_per_month"] / 10000 * v["kv_operations_usd_per_10k"]),
        ("Private endpoint and private DNS zones",
         f"{v['private_endpoints']:g} endpoint, {v['private_dns_zones']:g} zones",
         v["private_endpoints"] * v["private_endpoint_usd_per_hour"] * v["hours_per_month"]
         + v["private_dns_zones"] * v["private_dns_zone_usd_per_month"]),
        ("Container Registry, Standard", "1 registry", v["acr_standard_usd_per_day"] * v["hours_per_month"] / 24),
        ("Application Gateway WAF v2 (TLS gateway)", f"1 gateway, {v['appgw_capacity_units']:g} capacity units",
         v["hours_per_month"] * (v["appgw_waf_fixed_usd_per_hour"]
                                 + v["appgw_capacity_units"] * v["appgw_waf_capacity_unit_usd_per_hour"])),
    ]


def azure_hosting(v):
    """Hosting a year in AED: production at list price plus a test environment as an assumed share of it."""
    prod_usd_month = sum(x for _, _, x in azure_lines(v))
    prod = prod_usd_month * 12 * v["usd_aed_peg"]
    nonprod = prod * v["nonprod_share_of_prod"]
    return {"prod_usd_month": prod_usd_month, "prod_aed_year": prod, "nonprod_aed_year": nonprod,
            "total_aed_year": prod + nonprod}


def programme_volumes(spec, case="central"):
    """Screened people and actionable cases a year in Abu Dhabi by pathway (low, central or high inputs)."""
    idx = {"low": 0, "central": 1, "high": 2}[case]

    def pick(name):
        return range_of(spec, name)[idx]

    fit_results = spec["abu_dhabi_population_40_to_75"]["value"] * pick("fit_participation_annual")
    rows = [
        ("FIT positives", fit_results, fit_results * pick("fit_positivity_market")),
        ("Blood-based screening positives", pick("blood_tests_per_year"),
         pick("blood_tests_per_year") * pick("blood_test_positive_share")),
        ("Breast screening recalls", pick("mammograms_per_year"),
         pick("mammograms_per_year") * pick("mammogram_recall_share")),
        ("Cervical screening abnormal results", pick("cervical_screens_per_year"),
         pick("cervical_screens_per_year") * pick("cervical_abnormal_share")),
    ]
    return [{"pathway": n, "screened": s, "cases": c} for n, s, c in rows]


def operator_cost(v, screened, cases, pathways, hosting_aed_year, hosting_scale=1.0):
    """Operator's cost to serve a year in AED at programme scale, with the one-off items it amortises."""
    hosting = hosting_aed_year * hosting_scale + v["hosting_variable_aed_per_1000_cases_per_year"] * cases / 1000
    support_fte = (v["support_fte_base"] + v["support_fte_per_50k_members"] * screened / 50000
                   + v["support_fte_per_extra_pathway"] * (pathways - 1))
    lines = {
        "hosting (Azure UAE North list price, production and test)": hosting,
        "third-party licences": v["third_party_licences_aed_per_year"],
        "engineering and support": support_fte * v["support_fte_loaded_cost_aed_per_year"],
        "clinical safety officer": v["clinical_safety_fte"] * v["clinical_safety_loaded_cost_aed_per_year"],
        "ADHICS audit, penetration test, data protection review": v["compliance_aed_per_year"],
        "integration, one feed per provider group (amortised)":
            v["provider_groups"] * v["integration_one_off_aed_per_provider"] / v["amortisation_years"],
        "pathway builds beyond FIT (amortised)":
            (pathways - 1) * v["pathway_build_one_off_aed"] / v["amortisation_years"],
    }
    one_off = (v["provider_groups"] * v["integration_one_off_aed_per_provider"]
               + (pathways - 1) * v["pathway_build_one_off_aed"])
    total = sum(lines.values())
    amortised = (lines["integration, one feed per provider group (amortised)"]
                 + lines["pathway builds beyond FIT (amortised)"])
    return {"lines": lines, "total": total, "cash_opex": total - amortised, "one_off": one_off,
            "support_fte": support_fte}


def revenue_options(v, screened, cases, closed_share):
    """Annual revenue under each price point for a given scope."""
    return {
        "A: per screened member": screened * v["price_per_screened_member_per_year_aed"],
        "B1: per tracked case": cases * v["price_per_actionable_case_aed"],
        "B2: per closed case": cases * closed_share * v["price_per_closed_case_aed"],
        "L: programme licence": v["programme_licence_aed_per_year"],
    }


def buyer_value(spec, v, fit_cases, delay_shares, effort_rows, prog, or_label="10-12 month ORs",
                pair_factor=None, coordinator_cost=None):
    """What the buyer gets a year from the FIT pathway (the only one simulated). Cash items in AED, the rest as counts."""
    out = outcome_value(fit_cases, delay_shares, "literature_anchored", spec, or_label, "saudi", pair_factor)
    fte = capacity_fte(fit_cases, effort_rows, spec)["literature anchored, programme level"]
    coord = v["coordinator_loaded_cost_aed_per_fte_year"] if coordinator_cost is None else coordinator_cost
    gross_capacity = fte["coordinator"] * coord
    clinician_cost = -fte["owner_clinician"] * coord * v["wage_ratio_clinician"]
    specialist_cost = -fte["specialist"] * coord * v["wage_ratio_specialist"]
    silent = (prog["baseline"]["silent_loss_rate"] - prog["orchestrated"]["silent_loss_rate"]) * fit_cases
    return {"avoided_cost": out["cost_avoided"], "advanced_avoided": out["late_stage_avoided"],
            "coordinator_fte": fte["coordinator"], "clinician_fte": -fte["owner_clinician"],
            "specialist_fte": -fte["specialist"], "gross_capacity": gross_capacity,
            "clinician_cost": clinician_cost, "specialist_cost": specialist_cost,
            "net_labour": gross_capacity - clinician_cost - specialist_cost, "silent_loss_prevented": silent}


def pricing_model(spec, delay_shares, effort_rows, prog, overrides=None, case="central", or_label="10-12 month ORs",
                  pair_factor=None, hosting_scale=1.0):
    """Everything the pricing report needs, for one set of inputs. `overrides` replaces input values by name."""
    sp = {k: dict(s) for k, s in spec.items()}
    for name, value in (overrides or {}).items():
        sp[name]["value"] = value
    v = {k: s["value"] for k, s in sp.items()}
    vols = programme_volumes(sp, case)
    fit = vols[0]
    host = azure_hosting(v)
    closed = prog["orchestrated"]["closed_share"]
    scopes = {
        "FIT only": (fit["screened"], fit["cases"], 1),
        "programme, four pathways": (sum(r["screened"] for r in vols), sum(r["cases"] for r in vols),
                                     int(v["licence_pathways"])),
    }
    res = {"v": v, "volumes": vols, "hosting": host, "closed_share": closed, "scopes": {}}
    for label, (screened, cases, n) in scopes.items():
        cost = operator_cost(v, screened, cases, n, host["total_aed_year"], hosting_scale)
        rev = revenue_options(v, screened, cases, closed)
        if label == "FIT only":
            rev["L: programme licence"] = None  # the licence is sold for the programme, not one pathway
        res["scopes"][label] = {"screened": screened, "cases": cases, "pathways": n, "cost": cost, "revenue": rev}
    res["buyer"] = buyer_value(sp, v, fit["cases"], delay_shares, effort_rows, prog, or_label, pair_factor)
    return res


def _md(headers, rows, right_from=1):
    align = ["---" if i < right_from else "---:" for i in range(len(headers))]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(align) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _payback(upfront, annual):
    return f"{upfront / annual * 12:,.0f} months" if annual > 0 else "none (no positive annual cash)"


def load_outcomes(path):
    """docs/results/outcomes.json (M7-3), or None when it does not exist."""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def outcome_advanced(outcomes, fit_cases):
    """Advanced-stage (III-IV) cancers avoided a year for `fit_cases` positives, central and 95% PSA bounds, from the outcomes
    model (docs/results/outcomes.json, literature-anchored). This is the authoritative count; the cost model's own
    odds-ratio arithmetic (section 4.3) is used only for avoided treatment cost."""
    g = outcomes["scenarios"]["literature_anchored"]["groups"]["all"]
    k = fit_cases / 1000
    return (g["central"]["advanced_avoided"] * k, g["psa"]["advanced_avoided"]["p025"] * k,
            g["psa"]["advanced_avoided"]["p975"] * k)


def outcome_costs(outcomes, fit_cases, net_costs):
    """Net cost per death avoided and per QALY for each (label, net cost a year), from the outcomes model's literature-anchored
    per-1,000 results scaled to `fit_cases`. Intervals divide by the PSA bounds of deaths or QALYs (2.5th and 97.5th
    percentiles); the extra colonoscopies the outcomes model counts are shown as a second net cost."""
    g = outcomes["scenarios"]["literature_anchored"]["groups"]["all"]
    k = fit_cases / 1000
    col_unit = outcomes["parameters"]["colonoscopy_cost_aed"]["value"]
    out = {"deaths": g["central"]["deaths_avoided_5y"] * k, "qalys": g["central"]["qalys_disc"] * k,
           "deaths_lo": g["psa"]["deaths_avoided_5y"]["p025"] * k, "deaths_hi": g["psa"]["deaths_avoided_5y"]["p975"] * k,
           "qalys_lo": g["psa"]["qalys_disc"]["p025"] * k, "qalys_hi": g["psa"]["qalys_disc"]["p975"] * k,
           "extra_colonoscopies": g["central"]["extra_colonoscopies"] * k, "colonoscopy_unit": col_unit, "rows": []}
    out["colonoscopy_cost"] = out["extra_colonoscopies"] * col_unit
    for label, net in net_costs:
        out["rows"].append({"label": label, "net": net, "net_with_col": net + out["colonoscopy_cost"]})
    return out


def pricing_markdown(spec, delay_shares, effort_rows, prog, results_name, outcomes=None):
    """The generated block of business-case.md section 9 (markdown). Deterministic."""
    m = pricing_model(spec, delay_shares, effort_rows, prog)
    v, host, b = m["v"], m["hosting"], m["buyer"]
    peg = v["usd_aed_peg"]
    prog_s, fit_s = m["scopes"]["programme, four pathways"], m["scopes"]["FIT only"]
    out = [PRICING_BEGIN, "",
           f"Generated from `tools/pilot/inputs.yaml` (sections `azure`, `pathways`, `pricing`, `operator`, `buyer`, plus the "
           f"earlier sections) and `{results_name}` (synthetic). AED unless stated; USD at the peg of {peg:g}. Illustrative: "
           "not a quote, an offer or a forecast.", ""]

    out += ["**9.1 Hosting on Azure UAE North at list price** (production, the `deploy/bicep` defaults; pay-as-you-go, "
            "no reservation, no free grant)", ""]
    rows = [[item, size, f"{usd:,.0f}", money(usd * 12 * peg)] for item, size, usd in azure_lines(v)]
    rows.append(["**Production total**", "", f"**{host['prod_usd_month']:,.0f}**", f"**{money(host['prod_aed_year'])}**"])
    rows.append([f"Test environment ({v['nonprod_share_of_prod']:.0%} of production, assumed)", "", "",
                 money(host["nonprod_aed_year"])])
    rows.append(["**Hosting a year**", "", "", f"**{money(host['total_aed_year'])}**"])
    out += [_md(["item", "sizing", "USD a month", "AED a year"], rows, right_from=2), ""]
    assumed = v["hosting_fixed_aed_per_month"] * 12
    ratio = assumed / host["total_aed_year"]
    verdict = ("so the pilot model's hosting is close to list price" if 0.8 <= ratio <= 1.25 else
               "so the pilot model understates hosting" if ratio < 0.8 else "so the pilot model overstates hosting")
    out += [f"The earlier assumed hosting input (AED {v['hosting_fixed_aed_per_month']:,.0f} a month, AED {assumed:,.0f} a "
            f"year, used by the pilot model) is {ratio:.2f} times this list-price build-up, {verdict}. The pricing below "
            "uses the build-up. Database compute with a standby is the largest line; a reservation would cut it (not "
            "applied, no reservation price read).", ""]

    out += ["**9.2 Volumes a year in Abu Dhabi** (FIT from section 1; the other pathways are assumed or a programme "
            "target, and none is built or simulated)", ""]
    vol = {c: programme_volumes(spec, c) for c in ("low", "central", "high")}
    rows = []
    for i, r in enumerate(vol["central"]):
        rows.append([r["pathway"], money(r["screened"]), money(vol["low"][i]["cases"]), money(r["cases"]),
                     money(vol["high"][i]["cases"])])
    rows.append(["**All four**", f"**{money(prog_s['screened'])}**", money(sum(r['cases'] for r in vol['low'])),
                 f"**{money(prog_s['cases'])}**", money(sum(r['cases'] for r in vol['high']))])
    out += [_md(["pathway", "screened, central", "actionable cases, low", "central", "high"], rows), ""]

    out += ["**9.3 Operator cost to serve a year** (central volumes; every input assumed except the Azure unit prices)", ""]
    rows = [[k, money(fit_s["cost"]["lines"][k]), money(prog_s["cost"]["lines"][k])] for k in prog_s["cost"]["lines"]]
    rows.append(["**Total cost to serve**", f"**{money(fit_s['cost']['total'])}**", f"**{money(prog_s['cost']['total'])}**"])
    rows.append(["cost per actionable case", f"{fit_s['cost']['total'] / fit_s['cases']:,.1f}",
                 f"{prog_s['cost']['total'] / prog_s['cases']:,.1f}"])
    rows.append(["one-off investment (integrations, pathway builds)", money(fit_s["cost"]["one_off"]),
                 money(prog_s["cost"]["one_off"])])
    out += [_md(["line", "FIT only", "programme, four pathways"], rows), ""]

    out += ["**9.4 Price points and operator margin** (illustrative fees: AED "
            f"{v['price_per_screened_member_per_year_aed']:g} per screened member, AED {v['price_per_actionable_case_aed']:g} "
            f"per tracked case, AED {v['price_per_closed_case_aed']:g} per case closed with evidence (simulated closed share "
            f"{m['closed_share']:.1%}), and a programme licence of AED {money(v['programme_licence_aed_per_year'])} a year "
            f"for {v['licence_pathways']:g} pathways; margin = (revenue - cost to serve) / revenue)", ""]
    rows = []
    for k in prog_s["revenue"]:
        rf, rp = fit_s["revenue"][k], prog_s["revenue"][k]
        rows.append([k, "not offered" if rf is None else money(rf),
                     "-" if rf is None else f"{(rf - fit_s['cost']['total']) / rf:+.0%}", money(rp),
                     f"{(rp - prog_s['cost']['total']) / rp:+.0%}"])
    out += [_md(["price point", "FIT only: revenue", "margin", "four pathways: revenue", "margin"], rows), ""]

    cost_p = prog_s["cost"]
    be_case = cost_p["total"] / prog_s["cases"]
    be_closed = cost_p["total"] / (prog_s["cases"] * m["closed_share"])
    licence_margin = v["programme_licence_aed_per_year"] - cost_p["cash_opex"]
    out += ["**9.5 Operator break-even and payback** (programme, four pathways, central)", ""]
    out += [_md(["measure", "value"], [
        ["break-even fee per tracked case", f"AED {be_case:,.1f}"],
        ["break-even fee per closed case", f"AED {be_closed:,.1f}"],
        ["break-even programme licence", f"AED {money(cost_p['total'])} a year"],
        ["operator one-off investment", f"AED {money(cost_p['one_off'])}"],
        ["licence less cash running cost (before amortisation)", f"AED {money(licence_margin)} a year"],
        ["operator payback on the one-off investment, licence", _payback(cost_p["one_off"], licence_margin)],
        ["operator payback with the buyer's implementation fee",
         _payback(cost_p["one_off"] - v["implementation_fee_aed"], licence_margin)],
    ]), ""]

    lic, fit_fee = v["programme_licence_aed_per_year"], fit_s["revenue"]["B1: per tracked case"]
    adv_oc = outcome_advanced(outcomes, fit_s["cases"]) if outcomes is not None else None
    if adv_oc is not None:
        b = dict(b, advanced_avoided=adv_oc[0])
    hi = buyer_value(spec, v, fit_s["cases"], delay_shares, effort_rows, prog, ">12 month ORs")
    if adv_oc is not None:
        hi = dict(hi, advanced_avoided=adv_oc[0])
    out += ["**9.6 What the buyer gets a year from the FIT pathway** (the only pathway simulated; synthetic shares, "
            "observational odds ratios, assumed wages; literature-anchored, programme level)", ""]
    rows = [
        ["avoided first-year treatment cost, 10-12 month ORs (cash, years later)", money(b["avoided_cost"])],
        ["avoided first-year treatment cost, beyond-12-month ORs", money(hi["avoided_cost"])],
        [f"coordinator capacity freed ({b['coordinator_fte']:.1f} FTE at AED {money(v['coordinator_loaded_cost_aed_per_fte_year'])})",
         money(b["gross_capacity"])],
        [f"owner-clinician time consumed ({b['clinician_fte']:.1f} FTE at {v['wage_ratio_clinician']:g} times)",
         money(-b["clinician_cost"])],
        [f"specialist time consumed ({b['specialist_fte']:.1f} FTE at {v['wage_ratio_specialist']:g} times)",
         money(-b["specialist_cost"])],
        ["**net labour, wage-weighted**", f"**{money(b['net_labour'])}**"],
        *([["advanced-stage cancers avoided (count; outcomes model, central and 95% interval)",
            f"{adv_oc[0]:.0f} ({adv_oc[1]:.0f} to {adv_oc[2]:.0f})"]] if adv_oc is not None else
          [["advanced-stage cancers avoided (count; 10-12 and beyond-12-month ORs)",
            f"{b['advanced_avoided']:.1f} to {hi['advanced_avoided']:.1f}"]]),
        [f"positives no longer lost without anyone told (count; silent loss {prog['baseline']['silent_loss_rate']:.1%} to "
         f"{prog['orchestrated']['silent_loss_rate']:.1%})", money(b["silent_loss_prevented"])],
        ["IFHAS 15-working-day referral and 31-working-day colonoscopy clocks, measured per case", "not monetised"],
    ]
    out += [_md(["item", "AED or count a year"], rows), ""]

    out += ["**9.7 Buyer break-even and payback** (fee less avoided treatment cost, against what it buys; the cash return "
            "accrues to the treating system years after the fee)", ""]
    upfront = v["implementation_fee_aed"]
    rows = []
    for label, fee, ac, adv in (
            (f"B1 per tracked case, FIT only (AED {money(fit_fee)})", fit_fee, b["avoided_cost"], b["advanced_avoided"]),
            ("same, beyond-12-month ORs", fit_fee, hi["avoided_cost"], hi["advanced_avoided"]),
            (f"L programme licence, four pathways (AED {money(lic)}), FIT value only", lic, b["avoided_cost"],
             b["advanced_avoided"]),
            ("same, beyond-12-month ORs", lic, hi["avoided_cost"], hi["advanced_avoided"])):
        net = fee - ac
        rows.append([label, f"{ac / fee:.0%}", money(net),
                     money(net / adv) if net > 0 else "saves money",
                     f"{net / b['silent_loss_prevented']:,.0f}" if net > 0 else "saves money",
                     _payback(upfront, -net)])
    out += [_md(["fee", "avoided cost covers", "net cost a year", "net cost per advanced cancer avoided",
                 "net cost per positive no longer silently lost", f"payback of the AED {money(upfront)} implementation fee"],
                rows), ""]

    out += ["**9.8 Sensitivity** (one input at a time; programme licence, four pathways; buyer column uses the FIT value "
            "only, 10-12 month ORs unless the row says otherwise)", ""]
    base = pricing_model(spec, delay_shares, effort_rows, prog)

    def cells(mm, label):
        ps = mm["scopes"]["programme, four pathways"]
        lic_ = mm["v"]["programme_licence_aed_per_year"]
        net = lic_ - mm["buyer"]["avoided_cost"]
        adv_ = (outcome_advanced(outcomes, ps_fit(mm))[0] if outcomes is not None else mm["buyer"]["advanced_avoided"])
        return [label, money(ps["cost"]["total"]), f"{(lic_ - ps['cost']['total']) / lic_:+.0%}",
                f"{ps['cost']['total'] / ps['cases']:,.1f}", f"{mm['buyer']['avoided_cost'] / lic_:.0%}",
                money(net / adv_) if net > 0 else "saves money"]

    def ps_fit(mm):
        return mm["scopes"]["FIT only"]["cases"]

    rows = [cells(base, "**base case**")]
    sens = [
        ("volumes low (all pathways)", dict(case="low")),
        ("volumes high (all pathways)", dict(case="high")),
        ("licence price -25%", dict(overrides={"programme_licence_aed_per_year": lic * 0.75})),
        ("licence price +25%", dict(overrides={"programme_licence_aed_per_year": lic * 1.25})),
        ("hosting -50%", dict(hosting_scale=0.5)),
        ("hosting +100%", dict(hosting_scale=2.0)),
        ("support FTE per 50,000 screened halved",
         dict(overrides={"support_fte_per_50k_members": v["support_fte_per_50k_members"] / 2})),
        ("support FTE per 50,000 screened doubled",
         dict(overrides={"support_fte_per_50k_members": v["support_fte_per_50k_members"] * 2})),
        ("provider groups 3", dict(overrides={"provider_groups": 3})),
        ("provider groups 12", dict(overrides={"provider_groups": 12})),
        ("stage-pair factor low", dict(pair_factor=range_of(spec, "stage_pair_factor")[0])),
        ("stage-pair factor high", dict(pair_factor=range_of(spec, "stage_pair_factor")[2])),
        ("beyond-12-month ORs", dict(or_label=">12 month ORs")),
    ]
    for label, kw in sens:
        rows.append(cells(pricing_model(spec, delay_shares, effort_rows, prog, **kw), label))
    out += [_md(["case", "operator cost a year", "licence margin", "cost per actionable case",
                 "FIT avoided cost covers licence", "buyer net cost per advanced cancer avoided"], rows), ""]

    if outcomes is not None:
        oc = outcome_costs(outcomes, fit_s["cases"], [
            (f"B1 per tracked case, FIT only (AED {money(fit_fee)})", fit_fee - b["avoided_cost"]),
            (f"L programme licence, four pathways (AED {money(lic)}), FIT value only", lic - b["avoided_cost"])])

        def per(net, unit, lo, hi):
            if net <= 0:
                return "saves money"
            return f"{money(net / unit)} ({money(net / hi)} to {money(net / lo)})"
        out += ["**9.9 Net cost per death avoided and per QALY** (FIT pathway, literature-anchored; deaths and QALYs from "
                "[outcomes.md](../results/outcomes.md), M7-3: colorectal cancer deaths within 5 years avoided and QALYs "
                "discounted at 3%, from Corley 2017's delay odds ratios, Saudi and SEER stage survival and WHO UAE life "
                f"expectancy; for {money(fit_s['cases'])} positives a year that is {oc['deaths']:.1f} deaths "
                f"({oc['deaths_lo']:.1f} to {oc['deaths_hi']:.1f}) and {money(oc['qalys'])} QALYs ({money(oc['qalys_lo'])} to "
                f"{money(oc['qalys_hi'])}), 95% PSA intervals. Net cost is the fee less the avoided treatment cost of 9.7, "
                "10-12 month ORs; the second pair of columns adds the extra colonoscopies the outcomes model counts "
                f"({money(oc['extra_colonoscopies'])} a year at an assumed AED {money(oc['colonoscopy_unit'])} each), which "
                "this cost model otherwise leaves to the payer. Ranges divide by the PSA bounds. Illustrative.)", ""]
        rows = [[r["label"], money(r["net"]), per(r["net"], oc["deaths"], oc["deaths_lo"], oc["deaths_hi"]),
                 per(r["net"], oc["qalys"], oc["qalys_lo"], oc["qalys_hi"]),
                 per(r["net_with_col"], oc["deaths"], oc["deaths_lo"], oc["deaths_hi"]),
                 per(r["net_with_col"], oc["qalys"], oc["qalys_lo"], oc["qalys_hi"])] for r in oc["rows"]]
        out += [_md(["fee", "net cost a year", "per death avoided", "per QALY", "per death avoided, with colonoscopies",
                     "per QALY, with colonoscopies"], rows), ""]

    coverage = b["avoided_cost"] / lic
    out += [f"**Reading.** At the central inputs the programme licence costs the operator AED {money(cost_p['total'])} a year "
            f"to serve ({prog_s['cost']['total'] / prog_s['cases']:,.1f} per actionable case across "
            f"{money(prog_s['cases'])} cases), so a licence of AED {money(lic)} carries a "
            f"{(lic - cost_p['total']) / lic:.0%} margin. Avoided treatment cost from the FIT pathway alone covers "
            f"{coverage:.0%} of the licence ({hi['avoided_cost'] / lic:.0%} at the beyond-12-month odds ratios), and "
            f"wage-weighted labour is a net cost of AED {money(-b['net_labour'])} a year in the current routing, so neither pays "
            "the fee. The buyer pays for the remainder in safety and outcomes: about "
            f"{money(b['silent_loss_prevented'])} positives a year no longer lost without anyone told and "
            + (f"{adv_oc[0]:.0f} ({adv_oc[1]:.0f} to {adv_oc[2]:.0f}) advanced-stage cancers avoided (outcomes model, synthetic, "
               if adv_oc is not None else
               f"{b['advanced_avoided']:.0f} to {hi['advanced_avoided']:.0f} advanced-stage cancers avoided (synthetic, ")
            + "assumption-based), plus a per-case record against the IFHAS timelines"
            + (f", and in health terms about {oc['deaths']:.0f} colorectal cancer deaths avoided within 5 years for each year of "
               f"positives ({oc['deaths_lo']:.0f} to {oc['deaths_hi']:.0f}), at a net cost of about AED "
               f"{money(oc['rows'][1]['net'] / oc['qalys'])} per QALY on the licence before the extra colonoscopies and "
               f"AED {money(oc['rows'][1]['net_with_col'] / oc['qalys'])} with them (9.9)" if outcomes is not None else "")
            + ". The other three pathways add volume and cost here but no modelled value, because none is built or "
            "simulated.", "", PRICING_END]
    return "\n".join(out) + "\n"


def write_pricing_block(text, path=BUSINESS_CASE):
    doc = Path(path).read_text(encoding="utf-8")
    start, end = doc.index(PRICING_BEGIN), doc.index(PRICING_END) + len(PRICING_END)
    new = doc[:start] + text.rstrip("\n") + doc[end:]
    Path(path).write_text(new, encoding="utf-8")
    return new != doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inputs", default=str(DEFAULT_INPUTS), help="inputs file (default: tools/pilot/inputs.yaml)")
    ap.add_argument("--results", default=str(DEFAULT_RESULTS),
                    help="simulator results (default: docs/results/results.json in the repo)")
    ap.add_argument("--outcomes", default=str(DEFAULT_OUTCOMES),
                    help="health outcomes for --pricing section 9.9 (default: docs/results/outcomes.json; skipped if absent)")
    ap.add_argument("--commercial", action="store_true",
                    help="print the commercial-case extension (market, outcome value, capacity, benchmarks) instead of the pilot cost report")
    ap.add_argument("--pricing", action="store_true",
                    help="print the pricing and unit-economics block of docs/commercial/business-case.md section 9 (markdown)")
    ap.add_argument("--write", action="store_true", help="with --pricing: rewrite that block in business-case.md")
    args = ap.parse_args(argv)
    try:
        data, spec = load_inputs(args.inputs)
        shares = load_delay_shares(args.results)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    v = {k: s["value"] for k, s in spec.items()}
    cur = data.get("currency", "AED")
    if args.pricing:
        try:
            text = pricing_markdown(spec, shares, load_effort_minutes(args.results), load_programme_shares(args.results),
                                    Path(args.results).name, load_outcomes(args.outcomes))
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.write:
            changed = write_pricing_block(text)
            print(f"{BUSINESS_CASE.relative_to(REPO_ROOT)}: {'updated' if changed else 'unchanged'}")
        else:
            sys.stdout.write(text)
        return 0
    if args.commercial:
        try:
            print(f"Currency: {cur}. Inputs file: {Path(args.inputs).name}.")
            print_commercial(spec, shares, load_effort_minutes(args.results), Path(args.results).name)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    print("PILOT COST MODEL (illustrative)")
    print(f"Currency: {cur}. Inputs file: {Path(args.inputs).name}.")
    print("Every input below is an assumption, not a quote, forecast or measured value; the three wage ratios are")
    print("the exception (sourced-adjacent: published salary pages, not an official pay scale).\n")

    print("INPUTS")
    print(table(["input", "value", "status", "unit"],
                [[k, s["value"], s["status"], s["unit"]] for k, s in spec.items()
                 if s["section"] in LEGACY_SECTIONS], left=(0, 2, 3)))

    sizes = data["scenarios"]["screened_members_per_year"]
    res = [scenario(m, v) for m in sizes]
    head = ["per year"] + [f"{m:,} members" for m in sizes]

    def row(label, key, fmt=money):
        return [label] + [fmt(r[key]) for r in res]

    print("\nCOST TO SERVE, STEADY STATE (per year)")
    print(table(head, [
        row("actionable cases (FIT-positive)", "cases"),
        row("participating providers", "providers", lambda x: f"{x}"),
        row("hosting", "hosting"),
        row("third-party licences", "licences"),
        row("support (FTE x loaded cost)", "support"),
        row("integration (amortised)", "integration"),
        row("total cost to serve", "total"),
    ]))

    print("\nCOST PER UNIT")
    print(table(head, [
        row("cost per actionable case", "cost_per_case", lambda x: f"{x:,.1f}"),
        row("cost per screened member", "cost_per_member", lambda x: f"{x:,.2f}"),
    ]))

    print("\nCOORDINATOR TIME (assumed minutes per case, with vs without the engine)")
    print(table(head, [
        row("coordinator hours saved", "saved_hours"),
        row("value of time saved", "saved_value"),
    ]))
    print("All roles' minutes are valued at the coordinator rate in this table; the next section weights them by role.")

    effort_rows = load_effort_minutes(args.results)
    ratio_sets = [
        ("central", v["wage_ratio_clinician"], v["wage_ratio_specialist"]),
        ("low end", spec["wage_ratio_clinician"]["low"], spec["wage_ratio_specialist"]["low"]),
        ("high end", spec["wage_ratio_clinician"]["high"], spec["wage_ratio_specialist"]["high"]),
    ]
    print("\nWAGE-WEIGHTED STAFF TIME (coordinator-equivalent minutes per case; synthetic minutes x sourced-adjacent ratios)")
    print("Weighted minutes = coordinator + clinician ratio x owner-clinician + specialist ratio x specialist minutes.")
    print(f"Minutes by role are read from {Path(args.results).name} (effort, mean over replications). Ratios are to the coordinator")
    print(f"(1.0), from docs/evidence-wages.md section 4: clinician {v['wage_ratio_clinician']:g}, specialist {v['wage_ratio_specialist']:g}; the sensitivity")
    print(f"ends are clinician {spec['wage_ratio_clinician']['low']:g} to {spec['wage_ratio_clinician']['high']:g}, specialist "
          f"{spec['wage_ratio_specialist']['low']:g} to {spec['wage_ratio_specialist']['high']:g}. The nurse ratio ({v['wage_ratio_nurse']:g}, range "
          f"{spec['wage_ratio_nurse']['low']:g} to {spec['wage_ratio_nurse']['high']:g}) is not applied: the")
    print("simulator has no nurse role. Each orchestrated row is compared with the baseline of the same cases.\n")
    by_label = {label: m for label, m, _ in effort_rows}
    print(table(["minutes per case by role", "coordinator", "owner clinician", "specialist", "all roles", "change"], [
        [label, f"{m['coordinator']:.1f}", f"{m['owner_clinician']:.1f}", f"{m['specialist']:.1f}",
         f"{sum(m.values()):.1f}",
         "-" if base is None else f"{(sum(m.values()) / sum(by_label[base].values()) - 1):+.1%}"]
        for label, m, base in effort_rows]))
    print()
    head_w = ["coordinator-equivalent minutes"]
    for name, rc, rs in ratio_sets:
        head_w += [f"{name} ({rc:g}/{rs:g})", "change"]
    wrows = []
    for label, m, base in effort_rows:
        r = [label]
        for _, rc, rs in ratio_sets:
            w = weighted_minutes(m, rc, rs)
            r.append(f"{w:.1f}")
            r.append("-" if base is None else f"{(w / weighted_minutes(by_label[base], rc, rs) - 1):+.1%}")
        wrows.append(r)
    print(table(head_w, wrows))
    print("Ratios in each column header are clinician/specialist. Positive change = more weighted staff time than baseline.")

    print("\nCOMMERCIAL OPTIONS (per year; illustrative fees from the inputs file)")
    print(table(head, [
        row("A: fee per screened member", "fee_a"),
        row("A: provider net (saving - fee)", "net_a"),
        row("A: operator margin (fee - cost)", "margin_a"),
        row("B: fee per actionable case", "fee_b"),
        row("B: provider net (saving - fee)", "net_b"),
        row("B: operator margin (fee - cost)", "margin_b"),
    ]))

    p = pilot(v)
    print("\n90-DAY PILOT (enrolment window plus follow-up tail)")
    print(table(["item", "value"], [
        ["FIT results enrolled in window", f"{p['fits']:,.0f}"],
        ["expected actionable cases", f"{p['cases']:,.0f}"],
        ["days funded (window + tail)", f"{p['run_days']}"],
        ["hosting", money(p["hosting"])],
        ["third-party licences", money(p["licences"])],
        ["support", money(p["support"])],
        ["integration (one-off, not amortised)", money(p["integration"])],
        ["governance review (one-off)", money(p["governance"])],
        ["total pilot cost", money(p["total"])],
        ["pilot cost per actionable case", f"{p['cost_per_case']:,.0f}"],
    ]))

    sp = shadow_pilot(v)
    print("\nSHADOW PILOT (flat-file or HL7 v2 export, no outbound messages)")
    print(table(["item", "value"], [
        ["FIT results in window", f"{sp['fits']:,.0f}"],
        ["cases in window (FIT-positive)", f"{sp['cases']:,.0f}"],
        ["days", f"{sp['days']}"],
        ["hosting", money(sp["hosting"])],
        ["third-party licences", money(sp["licences"])],
        ["support", money(sp["support"])],
        ["integration (one-off, export mapping)", money(sp["integration"])],
        ["governance review (one-off)", money(sp["governance"])],
        ["total shadow pilot cost", money(sp["total"])],
        ["shadow pilot cost per case", f"{sp['cost_per_case']:,.0f}"],
    ]))

    print("\nVALUE SIDE: late-stage cancers potentially avoided (ILLUSTRATIVE, SYNTHETIC)")
    print("Simulated shares x published odds ratios; not a forecast, not a clinical claim; observational ORs,")
    print("confounding by indication; never-completers treated as if completing late.")
    print(f"Shares with no colonoscopy by day 270, read from {Path(args.results).name} (synthetic): "
          f"baseline {shares['baseline']:.1%}, literature-anchored orchestrated {shares['literature_anchored']:.1%}"
          + (f", upper-range orchestrated {shares['upper_range']:.1%}." if "upper_range" in shares else "."))
    print(f"On-time risk (assumed): any CRC {v['crc_risk_on_time']:.3f}, advanced stage {v['advanced_stage_risk_on_time']:.3f}.")
    vol_head = ["item", "per 1,000 cases"] + [f"{r['cases']:,.0f} cases a year" for r in res]
    vol_cases = [1000] + [r["cases"] for r in res]
    vrows = []
    for scen_key, scen_label in (("literature_anchored", "literature-anchored"), ("upper_range", "upper-range")):
        if scen_key not in shares:
            continue
        vrows.append([f"{scen_label}: cases moved to colonoscopy by day 270"]
                     + [f"{value_case(c, shares, scen_key, v, '10-12 month ORs')['moved']:,.1f}" for c in vol_cases])
        for case_label in ("10-12 month ORs", ">12 month ORs"):
            vals = [value_case(c, shares, scen_key, v, case_label) for c in vol_cases]
            vrows.append([f"  {case_label}: cancers diagnosed earlier"] + [f"{x['earlier']:,.2f}" for x in vals])
            vrows.append([f"  {case_label}: late-stage potentially avoided"] + [f"{x['late_stage']:,.2f}" for x in vals])
    print(table(vol_head, vrows))

    print("\nREAD THIS FIRST: every cost input is assumed (the wage ratios are sourced-adjacent). Cost per case")
    print("falls with volume because hosting, licences and the support base are fixed; the numbers show that")
    print("shape, not a price.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
