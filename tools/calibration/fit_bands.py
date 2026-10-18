"""Lognormal time-to-colonoscopy with a point mass for never-completers, from band shares (ticket B18, datasets 1 and 2).

Model: a positive FIT ends in a colonoscopy with probability (1 - pi); given that, the time T from the clock start to the
colonoscopy is lognormal(mu, sigma). Published tables give, among people who HAD a colonoscopy, the share within a few
cut points (Public Health Scotland: 4 and 8 weeks, and 31 and 62 days), and, separately, the share of positives who had one at all (-> pi).

Two cut points give two equations in (mu, sigma): z1 = (ln t1 - mu) / sigma and z2 = (ln t2 - mu) / sigma, so
sigma = ln(t2 / t1) / (z2 - z1) and mu = ln t1 - z1 * sigma. Exactly identified; the reported median is an independent check.
"""
from __future__ import annotations

import math
from typing import Sequence

import stats as S
import xlsxread as X


def lognormal_from_two_quantiles(t1: float, p1: float, t2: float, p2: float) -> tuple[float, float]:
    z1, z2 = S.norm_ppf(p1), S.norm_ppf(p2)
    sigma = math.log(t2 / t1) / (z2 - z1)
    return math.log(t1) - z1 * sigma, sigma


def lognormal_summary(mu: float, sigma: float) -> dict:
    q = lambda p: math.exp(mu + sigma * S.norm_ppf(p))
    return {"mu": mu, "sigma": sigma, "median": math.exp(mu), "p25": q(0.25), "p75": q(0.75), "p90": q(0.90),
            "mean": math.exp(mu + sigma * sigma / 2)}


def lognormal_cdf(t: float, mu: float, sigma: float) -> float:
    return S.norm_cdf((math.log(t) - mu) / sigma) if t > 0 else 0.0


def mixture_completion(t: float, mu: float, sigma: float, pi_never: float) -> float:
    """P(colonoscopy by day t) = (1 - pi) * F(t)."""
    return (1.0 - pi_never) * lognormal_cdf(t, mu, sigma)


# ---- Public Health Scotland KPI workbook ----------------------------------------------------------------------
def _find_row(rows: list[tuple[int, list]], pred) -> int | None:
    for r, v in rows:
        if pred(v):
            return r
    return None


def extract_phs(path) -> dict:
    book = X.read_xlsx(path)
    res: dict = {"kpi4": None, "a6": None}
    for key, sheet_name in (("kpi4", "KPI_4"), ("a6", "A6-Time from screening to colon")):
        sh = book[sheet_name]
        rows = X.rows(sh)
        cells = dict(rows)
        males = _find_row(rows, lambda v: len(v) >= 1 and v[0] == "Males")
        hdr_row = max(r for r, v in rows if r < males and len(v) > 3)            # the board names sit just above "Males"
        boards = [str(h) for h in cells[hdr_row][2:] if h]
        allp = _find_row(rows, lambda v: len(v) >= 1 and v[0] == "All persons")
        bands: dict = {}
        for r in range(allp + 1, allp + 7):          # three band rows, a blank row, then the median row
            v = cells.get(r)
            if v and len(v) > 2 and v[1]:
                bands[str(v[1]).strip()] = [float(x) for x in v[2:2 + len(boards)]]
        res[key] = {"boards": boards, "bands": bands}
    a4 = X.rows(book["A4-Numerical counts by HB"])
    hdr = _find_row(a4, lambda v: len(v) > 5 and v[1] == "NHS Board")
    cells = dict(a4)
    cols = [str(c) for c in cells[hdr]]
    ix = {c: cols.index(c) for c in ("Invites", "Positive tests", "Colonoscopy performed")}
    counts = {}
    r = hdr + 1
    while r in cells and cells[r][1]:
        counts[str(cells[r][1])] = {k: float(cells[r][i]) for k, i in ix.items()}
        r += 1
    res["a4_counts"] = counts
    # time series of positivity (KPI 3), Persons, for context
    ts = X.rows(book["TSData"])
    hdr_ts = _find_row(ts, lambda v: len(v) > 3 and v[0] == "KPI" and v[1] == "Sex")
    periods = [str(c) for c in dict(ts)[hdr_ts][2:] if c]
    res["kpi3_periods"] = periods
    for r, v in ts:
        if r > hdr_ts and v[0] in (3.0, "3.0", 3) and v[1] == "Persons":
            res["kpi3_persons"] = dict(zip(periods, [float(x) for x in v[2:2 + len(periods)]]))
    return res


def fit_phs(ex: dict) -> dict:
    """Per NHS board and for Scotland: lognormal from the (4 w, 8 w) shares and, as a second fit, the (31 d, 62 d) shares of
    appendix A6; pi from the colonoscopy counts."""
    out: dict = {"boards": {}, "note": "time from screening-test referral date to colonoscopy performed, among positives with a colonoscopy performed"}
    k4, a6 = ex["kpi4"], ex["a6"]
    b4 = {k: v for k, v in k4["bands"].items()}
    lab0 = next(l for l in b4 if l.startswith("0 to 4"))
    lab1 = next(l for l in b4 if l.startswith(">4 to 8"))
    labm = next(l for l in b4 if l.startswith("median"))
    a0 = next(l for l in a6["bands"] if l.startswith("0 to 31"))
    a1 = next(l for l in a6["bands"] if l.startswith(">31 to 62"))
    am = next(l for l in a6["bands"] if l.startswith("median"))
    for i, board in enumerate(k4["boards"]):
        s1 = k4["bands"][lab0][i] / 100.0
        s2 = s1 + k4["bands"][lab1][i] / 100.0
        mu, sg = lognormal_from_two_quantiles(28.0, s1, 56.0, s2)
        t1 = a6["bands"][a0][i] / 100.0
        t2 = t1 + a6["bands"][a1][i] / 100.0
        mu2, sg2 = lognormal_from_two_quantiles(31.0, t1, 62.0, t2)
        cnt = ex["a4_counts"].get(board)
        pi = 1.0 - cnt["Colonoscopy performed"] / cnt["Positive tests"] if cnt else None
        row = {"fit_weeks_bands": lognormal_summary(mu, sg), "fit_days_bands": lognormal_summary(mu2, sg2),
               "reported_median_days_kpi4": k4["bands"][labm][i] * 7.0, "reported_median_days_a6": a6["bands"][am][i],
               "share_within_4w": s1, "share_over_8w": 1.0 - s2, "pi_never_in_window": pi}
        if pi is not None:
            row["completion_from_referral"] = {str(d): mixture_completion(d, mu, sg, pi) for d in (90, 180, 360)}
        out["boards"][board] = row
    out["scotland"] = out["boards"]["Scotland"]
    # spread of the per-board medians (dispersion between boards)
    meds = sorted(r["fit_weeks_bands"]["median"] for b, r in out["boards"].items() if b != "Scotland")
    out["board_median_range_days"] = [meds[0], meds[-1]]
    out["kpi3_persons"] = ex.get("kpi3_persons")
    return out
