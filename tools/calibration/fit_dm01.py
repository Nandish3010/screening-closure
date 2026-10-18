"""NHS England Monthly Diagnostics (DM01) provider workbook: colonoscopy waiting stock by weekly band (ticket B18, dataset 4).

DM01 counts patients still waiting at month end by the time they have waited SO FAR (weeks 0-1 ... 12-13, 13+). That is the
age distribution of a stationary queue, not the distribution of completed waits. If completed waits T are lognormal(mu, sigma)
with density f and survival S, the age density of the waiting stock is S(a) / E[T] (the equilibrium distribution; the
completed-wait distribution seen from the stock is the length-biased one, t f(t) / E[T], and this is the same object viewed
from the other end). Its cdf has a closed form

    G(x) = x S(x) / E[T] + Phi((ln x - mu - sigma^2) / sigma),     E[T] = exp(mu + sigma^2 / 2).

(mu, sigma) are fitted by maximum likelihood on the 14 band counts (multinomial). The result is a BOUND, not a calibration
target: DM01 covers every colonoscopy referral in England (symptomatic, surveillance, screening), the clock starts at referral, and
removals other than tests also leave the list. As an independent check, Little's law: E[T] = stock / throughput.
"""
from __future__ import annotations

import math

import stats as S
import xlsread as L
import xlsxread as X

EDGES_WEEKS = list(range(0, 14))          # band k is [k, k+1) weeks for k = 0..12, then 13+


def age_cdf(x_days: float, mu: float, sigma: float) -> float:
    if x_days <= 0:
        return 0.0
    mean = math.exp(mu + sigma * sigma / 2)
    surv = 1.0 - S.norm_cdf((math.log(x_days) - mu) / sigma)
    return x_days * surv / mean + S.norm_cdf((math.log(x_days) - mu - sigma * sigma) / sigma)


def band_probs(mu: float, sigma: float) -> list[float]:
    cdf = [age_cdf(7.0 * w, mu, sigma) for w in EDGES_WEEKS]
    p = [cdf[k + 1] - cdf[k] for k in range(13)]
    p.append(1.0 - cdf[13])
    return p


def neg_loglik(theta, counts: list[float]) -> float:
    mu, ls = theta
    sigma = math.exp(ls)
    if not (-5 < mu < 10 and -3 < ls < 2):
        return 1e18
    p = band_probs(mu, sigma)
    return -sum(n * math.log(max(pk, 1e-300)) for n, pk in zip(counts, p))


def fit_stock(counts: list[float]) -> dict:
    best = None
    for mu0 in (2.0, 3.0, 4.0):
        for ls0 in (-0.5, 0.0, 0.5):
            x, v = S.nelder_mead(lambda t: neg_loglik(t, counts), [mu0, ls0], [0.3, 0.2])
            if best is None or v < best[1]:
                best = (x, v)
    (mu, ls), nll = best
    sigma = math.exp(ls)
    n = sum(counts)
    obs = [c / n for c in counts]
    fit = band_probs(mu, sigma)
    return {"mu": mu, "sigma": sigma, "median_days": math.exp(mu), "mean_days": math.exp(mu + sigma * sigma / 2),
            "p90_days": math.exp(mu + sigma * S.norm_ppf(0.9)), "share_over_6w_fit": sum(fit[6:]), "share_over_13w_fit": fit[13],
            "max_abs_band_error": max(abs(a - b) for a, b in zip(obs, fit)), "neg_loglik": nll}


def extract_dm01(path) -> dict:
    book = L.read_xls(path)
    rows = X.rows(book["Provider by Test"])
    hdr = next(v for r, v in rows if len(v) > 7 and v[6] == "Diagnostic Test Name")
    col = {str(h).strip().replace("\n", " "): i for i, h in enumerate(hdr) if h}
    band_cols = [col["0 <01 weeks"] + k for k in range(14)]
    act_col = next(i for h, i in col.items() if h.startswith("Waiting list tests"))
    bands = [0.0] * 14
    total = six = thirteen = activity = 0.0
    n_prov = 0
    for r, v in rows:
        if len(v) > act_col and v[6] == "Colonoscopy" and isinstance(v[col["Total Waiting List"]], float):
            n_prov += 1
            for k, c in enumerate(band_cols):
                bands[k] += v[c]
            total += v[col["Total Waiting List"]]
            six += v[col["Number waiting 6+ Weeks"]]
            thirteen += v[col["Number waiting 13+ Weeks"]]
            activity += v[act_col] if isinstance(v[act_col], float) else 0.0
    period = next((str(v[2]) for r, v in rows if len(v) > 2 and v[1] == "Period:"), "")
    return {"period": period, "providers": n_prov, "bands": bands, "total_waiting": total, "waiting_6plus": six,
            "waiting_13plus": thirteen, "waiting_list_tests_in_month": activity}


def fit_dm01(ex: dict) -> dict:
    bands = ex["bands"]
    n = sum(bands)
    res = fit_stock(bands)
    res.update({"period": ex["period"], "providers": ex["providers"], "total_waiting": ex["total_waiting"],
                "share_over_6w_observed": sum(bands[6:]) / n, "share_over_13w_observed": bands[13] / n,
                "band_check_sum_matches_total": abs(n - ex["total_waiting"]) < 1.0, "band_check_6plus_matches": abs(sum(bands[6:]) - ex["waiting_6plus"]) < 1.0})
    days = 31.0 if "March" in ex["period"] else 30.0
    if ex["waiting_list_tests_in_month"] > 0:
        res["little_law_mean_wait_days"] = ex["total_waiting"] / (ex["waiting_list_tests_in_month"] / days)
        res["waiting_list_tests_in_month"] = ex["waiting_list_tests_in_month"]
    res["observed_band_shares"] = [b / n for b in bands]
    res["fitted_band_shares"] = band_probs(res["mu"], res["sigma"])
    return res
