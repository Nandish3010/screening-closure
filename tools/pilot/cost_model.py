#!/usr/bin/env python3
"""Cost-per-case model for the 90-day pilot and for steady-state volumes.

Usage:  python3 tools/pilot/cost_model.py [--inputs tools/pilot/inputs.yaml] [--results docs/results/results.json]

Deterministic: no randomness, no clock, no network, standard library only. Every cost number it prints is
arithmetic on tools/pilot/inputs.yaml, and every input there is an assumption (see that file). The output
is illustrative; it is not a quote or a forecast. The one exception is the value table, which also reads the
simulated shares of cases with no colonoscopy by day 270 from docs/results/results.json (synthetic).
"""
import argparse
import json
import math
import sys
from pathlib import Path

DEFAULT_INPUTS = Path(__file__).with_name("inputs.yaml")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = REPO_ROOT / "docs" / "results" / "results.json"
ALLOWED_STATUS = ("assumed", "sourced")


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
    for section in ("inputs", "pilot", "shadow_pilot", "value"):
        for name, spec in data.get(section, {}).items():
            for field in ("value", "unit", "status", "basis"):
                if field not in spec:
                    raise ValueError(f"input '{name}' is missing '{field}'")
            if spec["status"] not in ALLOWED_STATUS:
                raise ValueError(f"input '{name}' has status '{spec['status']}'; use one of {ALLOWED_STATUS}")
            flat[name] = spec
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


def value_case(cases, shares, scenario_key, v, case_label):
    """Cancers potentially avoided for `cases` positive FIT cases (illustrative, synthetic shares x published ORs)."""
    suffix = "10_to_12_months" if case_label == "10-12 month ORs" else "over_12_months"
    p_any, p_adv = v["crc_risk_on_time"], v["advanced_stage_risk_on_time"]
    excess_any = odds_ratio_risk(p_any, v["or_any_crc_" + suffix]) - p_any
    excess_adv = odds_ratio_risk(p_adv, v["or_advanced_stage_" + suffix]) - p_adv
    moved = (shares["baseline"] - shares[scenario_key]) * cases
    return {"moved": moved, "earlier": moved * excess_any, "late_stage": moved * excess_adv}


# --- output -------------------------------------------------------------------------------------------------

def money(x):
    return f"{x:,.0f}"


def table(headers, rows, left=(0,)):
    widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    def fmt(r):
        return "  ".join(str(c).ljust(widths[i]) if i in left else str(c).rjust(widths[i]) for i, c in enumerate(r)).rstrip()
    return "\n".join([fmt(headers), "  ".join("-" * w for w in widths)] + [fmt(r) for r in rows])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inputs", default=str(DEFAULT_INPUTS), help="inputs file (default: tools/pilot/inputs.yaml)")
    ap.add_argument("--results", default=str(DEFAULT_RESULTS),
                    help="simulator results (default: docs/results/results.json in the repo)")
    args = ap.parse_args(argv)
    try:
        data, spec = load_inputs(args.inputs)
        shares = load_delay_shares(args.results)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    v = {k: s["value"] for k, s in spec.items()}
    cur = data.get("currency", "AED")

    print("PILOT COST MODEL (illustrative)")
    print(f"Currency: {cur}. Inputs file: {Path(args.inputs).name}.")
    print("Every input below is an assumption, not a quote, forecast or measured value.\n")

    print("INPUTS")
    print(table(["input", "value", "status", "unit"],
                [[k, s["value"], s["status"], s["unit"]] for k, s in spec.items()], left=(0, 2, 3)))

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

    print("\nREAD THIS FIRST: all inputs are assumed. Cost per case falls with volume because hosting,")
    print("licences and the support base are fixed; the numbers show that shape, not a price.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
