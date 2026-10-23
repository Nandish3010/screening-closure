"""Held-out validation targets for the simulator (ticket B20, milestone M6).

    python3 -I tools/calibration/validation_targets.py build   # parse the local external files, write validation_targets.json
    python3 -I tools/calibration/validation_targets.py check   # re-parse and fail on any difference from the committed file

Two kinds of target are assembled here:

* **parsed** from files under data/external/ (untrusted; read only by the parsers in this file, run in isolated mode, after a sha256 check
  against tools/calibration/manifest.json): Public Health Scotland KPI workbook, NHS England BCSP 2024-25 tables, and the Bowel Cancer Australia
  summary of the AIHW NBCSP 2026 report (a secondary source, because the AIHW site blocks automated access);
* **transcribed** published figures (US cohorts and trials) with the URL, the table or sentence they came from and how they were checked.

Nothing here is fitted: every number is an observation that `simulator/sim/validation.py` compares the simulator against. For each target
`independence` says whether any simulator parameter was set from the same numbers (see docs/results/validation.md, section "Targets").
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibrate as CAL  # noqa: E402  (sha256 + manifest helpers)
import fit_bands as FB  # noqa: E402
import sources as SRC  # noqa: E402
import xlsxread as X  # noqa: E402

OUT = SRC.ROOT / "tools" / "calibration" / "validation_targets.json"


# ------------------------------------------------------------------ parsers (tested on fixtures in tests/test_validation_targets.py)
def parse_aihw_summary_html(text: str) -> dict:
    """Figures from the Bowel Cancer Australia page that summarises the AIHW NBCSP monitoring report 2026.

    Extracted by pattern from the visible text only: positives and positivity in 2024, the share with a colonoscopy within the
    recommended 30 days (and the 2023 value), and the state range of waiting days. Raises ValueError if a pattern is absent, so a changed
    page fails loudly instead of silently returning nothing.
    """
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"\s+", " ", t)

    def need(pat: str):
        m = re.search(pat, t)
        if not m:
            raise ValueError(f"pattern not found: {pat}")
        return m

    pos = need(r"In (\d{4}), ([\d,]+) people received a positive screening result \(([\d.]+)%\)")
    c30 = need(r"only ([\d.]+)% \(\d{4}: ([\d.]+)%\) were recorded as having received a colonoscopy within the recommended 30-day")
    wait = need(r"waited between (\d+) days \((\w+)\) and (\d+) days \((\w+)\)")
    return {
        "year": int(pos.group(1)), "positives": int(pos.group(2).replace(",", "")), "positivity_pct": float(pos.group(3)),
        "colonoscopy_within_30d_pct": float(c30.group(1)), "colonoscopy_within_30d_pct_prev_year": float(c30.group(2)),
        "wait_days_state_min": int(wait.group(1)), "wait_days_state_min_state": wait.group(2),
        "wait_days_state_max": int(wait.group(3)), "wait_days_state_max_state": wait.group(4),
    }


def parse_bcsp_providers(book: dict) -> dict:
    """England BCSP Table 12 (S11 diagnostic procedure uptake): the screening-provider block (rows with a region code in column 1), the
    published regional block below it, and the England row. Uptake = Attendance / Definitive FOBt+ Patients."""
    rows = X.rows(book["Table 12"])
    cells = dict(rows)
    hdr1 = next(r for r, v in rows if "Region Code" in v[:3] and "Attendance" in v)
    h1 = [str(c) for c in cells[hdr1]]
    i_att, i_den, i_prov = h1.index("Attendance"), h1.index("Definitive FOBt+ Patients"), h1.index("Screening Provider")
    prov: dict = {}
    r = hdr1 + 1
    while r in cells and isinstance(cells[r][0], str) and cells[r][0].strip():
        v = cells[r]
        if isinstance(v[i_att], float) and isinstance(v[i_den], float) and v[i_den] > 0:
            prov[str(v[i_prov]).strip()] = [v[i_att], v[i_den]]
        r += 1
    hdr2 = next((rr for rr, v in rows if rr > r and len(v) > 4 and v[3] == "Region Code" and "Attendance" in v), None)
    if not prov or hdr2 is None:
        raise ValueError("Table 12 layout not recognised")
    h2 = [str(c) for c in cells[hdr2]]
    j_name, j_att, j_den = h2.index("Region"), h2.index("Attendance"), h2.index("Definitive FOBt+ Patients")
    region: dict = {}
    england = None
    r = hdr2 + 1
    while r in cells and cells[r][j_name]:
        v = cells[r]
        pair = [v[j_att], v[j_den]]
        if str(v[j_name]).strip() == "England":
            england = pair
        else:
            region[str(v[j_name]).strip()] = pair
        r += 1
    if england is None:
        raise ValueError("no England row in Table 12")
    ups = [a / d for a, d in prov.values()]
    return {"providers": len(prov), "provider_uptake_min": round(min(ups), 6), "provider_uptake_max": round(max(ups), 6),
            "region_uptake": {k: round(a / d, 6) for k, (a, d) in sorted(region.items())},
            "sum_attendance": england[0], "sum_definitive_positive": england[1]}


def parse_phs_validation(book_extract: dict) -> dict:
    """Scotland (all persons) from the output of fit_bands.extract_phs: band shares among those with a colonoscopy, reported medians,
    colonoscopy counts, and the between-board spread of the reported medians."""
    k4, a6 = book_extract["kpi4"], book_extract["a6"]
    lab0 = next(l for l in k4["bands"] if l.startswith("0 to 4"))
    lab1 = next(l for l in k4["bands"] if l.startswith(">4 to 8"))
    labm = next(l for l in k4["bands"] if l.startswith("median"))
    am = next(l for l in a6["bands"] if l.startswith("median"))
    ix = k4["boards"].index("Scotland")
    boards = [b for b in k4["boards"] if b != "Scotland"]
    meds = sorted(k4["bands"][labm][k4["boards"].index(b)] * 7.0 for b in boards)
    cnt = book_extract["a4_counts"]["Scotland"]
    return {
        "share_within_4w": round(k4["bands"][lab0][ix] / 100.0, 6),
        "share_within_8w": round((k4["bands"][lab0][ix] + k4["bands"][lab1][ix]) / 100.0, 6),
        "reported_median_days_kpi4": round(k4["bands"][labm][ix] * 7.0, 6),
        "reported_median_days_a6": round(a6["bands"][am][a6["boards"].index("Scotland")], 6),
        "positive_tests": cnt["Positive tests"], "colonoscopies_performed": cnt["Colonoscopy performed"],
        "board_median_days_min": round(meds[0], 6), "board_median_days_max": round(meds[-1], 6), "boards": len(boards),
    }


# ------------------------------------------------------------------ transcribed literature (kept in code so each carries its provenance)
def _lit() -> tuple[list[dict], list[dict]]:
    corley = "https://jamanetwork.com/journals/jama/fullarticle/2620087"
    targets = [
        {
            "id": "issaka-sf-safety-net", "title": "Integrated safety-net system, San Francisco (Issaka 2017)",
            "kind": "cohort", "arm": "baseline", "variants": [{"name": "unadjusted", "overrides": {}}], "primary_variant": "unadjusted",
            "n": 2238, "independence": "independent",
            "independence_note": "Not used for any parameter. The 184-day median is listed among the range of medians in params.yaml notes only.",
            "setting_note": "US safety-net, FIT-positive adults 50-75, April 2012 to February 2015; usual care (no study intervention). No setting adjustment: Mohl is also a US usual-care cohort.",
            "source": {"url": "https://doi.org/10.1038/ajg.2016.555", "reference": "Am J Gastroenterol 2017;112(2):375-382; read 2026-10-23 at stacks.cdc.gov/view/cdc/79501 and in docs/evidence.md section 3",
                       "grade": "P", "licence": "published figures"},
            "observed": {"completion": [{"day": 360, "k": 1245, "n": 2238}], "median": 184.0, "p25": 140.0, "p75": 232.0},
        },
        {
            "id": "seamar-fqhc", "title": "Sea Mar FQHC, Washington State (Sea Mar stepwise analysis 2021)",
            "kind": "cohort", "arm": "baseline", "variants": [{"name": "unadjusted", "overrides": {}}], "primary_variant": "unadjusted",
            "n": 711, "independence": "partial",
            "independence_note": "The completion curve (22.9, 37.1, 42.8) was not used: baseline completion was calibrated to Mohl. But the median (83) and IQR (52-131) ARE the sourced parameters time_to_colonoscopy_median_days / _iqr_days, so the timing rows of this target are an in-sample check (reported, flagged, and excluded from the independent count).",
            "setting_note": "US FQHC usual care, 711 abnormal FIT 2017-2018. No setting adjustment.",
            "source": {"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/", "reference": "BMC Gastroenterol 2021; figures from docs/evidence.md sections 2 and 3 (graded P there; the search summary of 2026-10-23 repeats 43% at 1 year and 83 (52-131))",
                       "grade": "P", "licence": "published figures"},
            "observed": {"completion": [{"day": 90, "k": 163, "n": 711}, {"day": 180, "k": 264, "n": 711}, {"day": 360, "k": 304, "n": 711}],
                         "median": 83.0, "p25": 52.0, "p75": 131.0},
            "in_sample_rows": ["median", "p25", "p75"],
        },
        {
            "id": "kpnw-usual-care", "title": "Kaiser Permanente Northwest precision-navigation RCT, usual-care arm (Coronado 2021)",
            "kind": "cohort", "arm": "baseline", "variants": [{"name": "unadjusted", "overrides": {}}], "primary_variant": "unadjusted",
            "n": 215, "independence": "partial",
            "independence_note": "Completion at 180 days is not an input. The 15-day navigation time reduction (88 to 73 days) in params.yaml comes from this same trial, so the usual-care median (88) is partly in-sample and is flagged; the target is also a more integrated system than the Mohl average.",
            "setting_note": "Integrated health system (Kaiser Permanente Northwest), patients predicted to have a colonoscopy probability <= 70%, usual-care arm. No setting adjustment (a harder-than-average subset, so a simulator that matches Mohl should over-predict).",
            "source": {"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9273475/", "reference": "Cancer Epidemiol Biomarkers Prev 2021; Results text, read 2026-10-23: 180-day cumulative completion 64.9% (usual care, n=215 analysed), median 88 days",
                       "grade": "P", "licence": "published figures"},
            "observed": {"completion": [{"day": 180, "k": 140, "n": 215}], "median": 88.0},
            "in_sample_rows": ["median"],
        },
        {
            "id": "azulay-control", "title": "Azulay SMS-reminder RCT for positive FOBT, Israel, control arm (2019)",
            "kind": "cohort", "arm": "baseline", "variants": [{"name": "unadjusted", "overrides": {}}], "primary_variant": "unadjusted",
            "n": 238, "independence": "independent",
            "independence_note": "Not used for any parameter (the SMS effect was fitted to Brazilian no-show data and Lam 2021).",
            "setting_note": "Israeli HMO, positive FOBT (not FIT), routine care with a physician alert. Test type differs; no setting adjustment.",
            "source": {"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6379817/", "reference": "JMIR mHealth uHealth 2019, read 2026-10-23: control 112 of 238 (47.1%) had a colonoscopy within 120 days",
                       "grade": "P", "licence": "published figures"},
            "observed": {"completion": [{"day": 120, "k": 112, "n": 238}]},
        },
        {
            "id": "corley-kaiser", "title": "Kaiser Permanente N and S California, integrated screening programme (Corley 2017)",
            "kind": "cohort", "arm": "orchestrated",
            "variants": [{"name": "literature-anchored orchestrated", "scenario": "literature_anchored", "overrides": {}},
                         {"name": "upper-range orchestrated", "scenario": "upper_range", "overrides": {}}],
            "primary_variant": "literature-anchored orchestrated",
            "n": 81518, "independence": "partial",
            "independence_note": "Corley's 3, 6 and 12-month completion (74.2, 80.6, 83.2) are the integrated_ref parameters that cap the orchestrated arm, so those rows are in-sample. The 30-day and 2-month completion and the median/IQR (37, 23-62) are not used anywhere.",
            "setting_note": "The question is whether an actively managed pathway run by our orchestrated arm reaches what a real integrated, actively managed programme achieves, so the orchestrated arm (not the baseline) is compared. Kaiser also has in-house colonoscopy capacity and a registry, which the simulator does not.",
            "source": {"url": corley, "reference": "JAMA 2017;317(16):1631; figures transcribed from docs/evidence.md sections 2 and 3 (graded P there); the article was not re-opened for B20 (paywalled to the fetch tool)",
                       "grade": "P (transcribed)", "licence": "published figures"},
            "observed": {"completion": [{"day": 30, "p": 0.333, "n": 81518}, {"day": 60, "p": 0.636, "n": 81518}, {"day": 90, "p": 0.742, "n": 81518},
                                        {"day": 180, "p": 0.806, "n": 81518}, {"day": 360, "p": 0.832, "n": 81518}],
                         "median": 37.0, "p25": 23.0, "p75": 62.0},
            "in_sample_rows": ["completion@90", "completion@180", "completion@360"],
        },
    ]
    trials = [
        {
            "id": "azulay-sms", "title": "Azulay 2019: SMS reminders after positive FOBT (Israel), 120 days", "intervention": "reminder",
            "day": 120, "treated": {"k": 163, "n": 232}, "control": {"k": 112, "n": 238}, "scenario": "literature_anchored",
            "independence": "independent",
            "independence_note": "Not used for calibration. Our orchestrated arm is a bundle (ownership, reminders, escalation), the trial an SMS alone, so this is an upper-side test: the simulated ratio should not be far below the trial's.",
            "source": {"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6379817/", "reference": "JMIR mHealth uHealth 2019; 163/232 vs 112/238 within 120 days; unadjusted OR 2.17 (1.49-3.17)", "grade": "P", "licence": "published figures"},
        },
        {
            "id": "kpnw-navigation", "title": "Coronado 2021: precision navigation RCT (Kaiser NW), 180 days", "intervention": "navigation",
            "day": 180, "treated": {"k": 151, "n": 200}, "control": {"k": 140, "n": 215}, "scenario": "literature_anchored",
            "independence": "partial",
            "independence_note": "The pooled RR 1.27 that fixes programme_reach (J Gen Intern Med 2026 meta-analysis) pooled navigation studies, and this trial may be among them (the abstract does not list them); the 15-day median reduction comes from it. Counts are rounded from the reported 75.5% and 64.9% (not published).",
            "source": {"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9273475/", "reference": "180-day cumulative completion 75.5% (n=200) vs 64.9% (n=215), HR 1.35 (1.07-1.72)", "grade": "P", "licence": "published figures"},
        },
        {
            "id": "annals-navigation-2025", "title": "Coronado 2025: navigation RCT in a 32-clinic FQHC network, 12 months", "intervention": "navigation",
            "day": 360, "treated": {"k": 264, "n": 479}, "control": {"k": 205, "n": 488}, "scenario": "literature_anchored",
            "independence": "not independent",
            "independence_note": "Cited in the source field of orchestration.programme_reach as the agreeing 12-month RCT, so it was read while calibrating the effect. Reported for completeness and NOT counted as independent. Counts are rounded from the adjusted 55.1% and 42.1%.",
            "source": {"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC13007779/", "reference": "Ann Intern Med 2025;178(5):645; 55.1% (50.3-59.7) vs 42.1% (37.5-46.8), RD 13.0 pp (6.5-19.4), adjusted OR 1.69 (1.30-2.19)", "grade": "P", "licence": "published figures"},
        },
    ]
    return targets, trials


# ------------------------------------------------------------------ build
def _managed_variants() -> list[dict]:
    adj = {"staff.forget_prob": 0.0, "referral_rejection_rate": 0.0, "handoff.no_response_prob": 0.0}
    return [{"name": "unadjusted", "overrides": {}},
            {"name": "programme-managed (no forgotten steps, automatic referral)", "overrides": adj}]


def build() -> dict:
    man = CAL.manifest()
    phs_p, phs_sha = CAL.locate(man, "phs")
    bc_p, bc_sha = CAL.locate(man, "nhs-bcsp")
    au_p, au_sha = CAL.locate(man, "bca-nbcsp2026")
    phs = parse_phs_validation(FB.extract_phs(phs_p))
    eng = parse_bcsp_providers(X.read_xlsx(bc_p))
    au = parse_aihw_summary_html(au_p.read_text(errors="replace"))
    cohorts, trials = _lit()
    mv = _managed_variants()
    nhs = "Contains public sector information licensed under the Open Government Licence v3.0."
    cohorts += [
        {
            "id": "phs-scotland", "title": "Public Health Scotland bowel screening, invites 2022-24 (national programme)",
            "kind": "cohort", "arm": "baseline", "variants": mv, "primary_variant": mv[1]["name"],
            "n": int(phs["positive_tests"]), "independence": "independent",
            "independence_note": "The workbook gave reference-only keys (time_to_colonoscopy_managed_ref_*, colonoscopy_uptake_after_positive_scotland) that the simulator does not read, so no simulator behaviour was set from it. The reference keys do quote these numbers, so this is out of sample for the model but not a blind test.",
            "setting_note": "Managed national programme: the programme issues the referral and owns follow-up. Adjusted variant removes the baseline's forgotten steps, referral rejection and unanswered referrals. The Scottish clock starts at the screening-test REFERRAL date (a few days to two weeks after the result), the simulator's at the result, which biases the comparison against the simulator on the timing rows.",
            "source": {"url": "https://publichealthscotland.scot/media/31821/2025-03-04-bowel-screening-kpi-report.xlsx", "reference": "KPI_4, A6 and A4 sheets, Scotland / all persons; sha256 " + phs_sha, "grade": "P (parsed)", "licence": "Open Government Licence v3.0"},
            "observed": {"completion": [{"day": 360, "k": int(phs["colonoscopies_performed"]), "n": int(phs["positive_tests"])}],
                         "completer_share": [{"day": 28, "p": phs["share_within_4w"]}, {"day": 56, "p": phs["share_within_8w"]}],
                         "median": phs["reported_median_days_kpi4"]},
            "context": {"board_median_days_min": phs["board_median_days_min"], "board_median_days_max": phs["board_median_days_max"], "boards": phs["boards"],
                        "attribution": SRC.SOURCES[0]["attribution"]},
        },
        {
            "id": "nhs-england-bcsp", "title": "NHS England BCSP 2024-25, diagnostic procedure uptake after a positive FIT (national programme)",
            "kind": "cohort", "arm": "baseline", "variants": mv, "primary_variant": mv[1]["name"],
            "n": int(eng["sum_definitive_positive"]), "independence": "independent",
            "independence_note": "Same position as Scotland: colonoscopy_uptake_after_positive_england (0.76) is a reference key the simulator does not read.",
            "setting_note": "Managed national programme with automatic referral; the table has uptake only, no time axis. Provider-level spread is reported as context (the simulator has one parameter set, so it cannot reproduce between-provider variation).",
            "source": {"url": "https://assets.publishing.service.gov.uk/media/6a4e6212f79c9db2eb438f07/Bowel_Cancer_Screening_Annual_Report_Tables_2024-25.xlsx", "reference": "Table 12 (S11), sum over screening providers; sha256 " + bc_sha, "grade": "P (parsed)", "licence": "Open Government Licence v3.0"},
            "observed": {"completion": [{"day": 360, "k": int(eng["sum_attendance"]), "n": int(eng["sum_definitive_positive"])}]},
            "context": {"providers": eng["providers"], "provider_uptake_min": eng["provider_uptake_min"], "provider_uptake_max": eng["provider_uptake_max"],
                        "region_uptake": eng["region_uptake"], "attribution": nhs + " Source: NHS England."},
        },
        {
            "id": "aihw-nbcsp-2026-secondary", "title": "Australia NBCSP, AIHW monitoring report 2026 via a secondary summary (30-day colonoscopy share)",
            "kind": "cohort", "arm": "baseline", "variants": [{"name": "unadjusted", "overrides": {}}], "primary_variant": "unadjusted",
            "n": au["positives"], "independence": "independent",
            "independence_note": "Not used for any parameter. SECONDARY source (AIHW blocked; see tools/calibration/manifest.json not_downloaded): graded S, so the verdict is provisional.",
            "setting_note": "National programme in which the participant's GP arranges the colonoscopy; the share is of people with a positive result, window 30 days from the positive result. The state waiting range is reported as context (the statistic, median or mean, is not stated on the page).",
            "source": {"url": SRC.SOURCES[4]["url"], "reference": f"Bowel Cancer Australia summary of AIHW NBCSP monitoring report 2026; sha256 {au_sha}", "grade": "S (secondary)", "licence": SRC.SOURCES[4]["licence"]},
            "observed": {"completion": [{"day": 30, "p": au["colonoscopy_within_30d_pct"] / 100.0, "n": au["positives"]}]},
            "context": {"wait_days_state_min": au["wait_days_state_min"], "wait_days_state_max": au["wait_days_state_max"],
                        "wait_days_state_min_state": au["wait_days_state_min_state"], "wait_days_state_max_state": au["wait_days_state_max_state"],
                        "colonoscopy_within_30d_pct_prev_year": au["colonoscopy_within_30d_pct_prev_year"], "year": au["year"]},
        },
    ]
    return {"schema": 1, "note": "Held-out validation targets (ticket B20). Built by tools/calibration/validation_targets.py; see docs/results/validation.md.",
            "cohort_targets": cohorts, "effect_trials": trials,
            "attempted_not_obtained": [
                {"id": "aihw-nbcsp-tables", "reason": "AIHW data tables: HTTP 403 for curl and WebFetch on 2026-10-23; not bypassed. Secondary summary used instead."},
                {"id": "dutch-programme-30-60-90", "reason": "A search on 2026-10-23 found the programme targets (intake within 15 working days) but no published 30/60/90-day proportions; not used."},
            ]}


def dumps(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "build"
    text = dumps(build())
    if cmd == "build":
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(SRC.ROOT)}")
        return 0
    if cmd == "check":
        if not OUT.exists() or OUT.read_text() != text:
            print("validation_targets.json differs from a fresh parse of the local files")
            return 1
        print("validation_targets.json matches a fresh parse of the local files")
        return 0
    raise SystemExit("usage: validation_targets.py build|check")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
