#!/usr/bin/env python3
"""Cost-per-case model for the 90-day pilot and for steady-state volumes.

Usage:  python3 tools/pilot/cost_model.py [--inputs tools/pilot/inputs.yaml]

Deterministic: no randomness, no clock, no network, standard library only. Every number it prints is
arithmetic on tools/pilot/inputs.yaml, and every input there is an assumption (see that file). The output
is illustrative; it is not a quote, a forecast or a result from the simulator.
"""
import argparse
import math
import sys
from pathlib import Path

DEFAULT_INPUTS = Path(__file__).with_name("inputs.yaml")
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
    for section in ("inputs", "pilot"):
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
    args = ap.parse_args(argv)
    try:
        data, spec = load_inputs(args.inputs)
    except (OSError, ValueError) as exc:
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

    print("\nREAD THIS FIRST: all inputs are assumed. Cost per case falls with volume because hosting,")
    print("licences and the support base are fixed; the numbers show that shape, not a price.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
