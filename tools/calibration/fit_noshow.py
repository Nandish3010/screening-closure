"""Medical Appointment No Shows (Vitoria, Brazil, 2016; OpenML 43439, CC BY-NC-SA 4.0): relative effects only (ticket B18, dataset 5).

Licence note: the data are non-commercial, share-alike. This project publishes ONLY the fitted coefficients below, with
attribution, never the records, and never uses the data in the product.

Rows kept: lead time (appointment date minus scheduled date, calendar days) of at least 3 days, because SMS reminders are only sent
to appointments booked at least that far ahead, so shorter leads would compare SMS recipients against a different kind of booking.
Age outside 0-110 is dropped (one -1 row exists).

Models (pure Python, deterministic):
  1. pooled logistic regression of no-show on SMS_received, ln(lead days) and age (per decade, centred), by Newton-Raphson;
     the SMS effect is reported as an odds ratio and as a standardised relative reduction in no-show probability
     1 - mean(p | SMS=1) / mean(p | SMS=0), with a delta-method 95% interval;
  2. random-intercept logistic regression (patients with at least two kept appointments), fitted by maximising the marginal
     likelihood with 12-point Gauss-Hermite quadrature (Nelder-Mead over the four coefficients and ln tau); the patient-level
     empirical-Bayes view of the same model is the between-patient SD tau on the logit scale. The conditional SMS odds ratio is
     reported next to the pooled one.
The colonoscopy no-show BASE rates in params.yaml are not touched: this population is Brazilian primary care.
"""
from __future__ import annotations

import math
from datetime import date

import stats as S

MIN_LEAD = 3
AGE_CENTRE = 37.0
GH_NODES = 12


def _days(iso: str) -> int:
    y, m, d = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
    return date(y, m, d).toordinal()


def parse_arff(path) -> list[dict]:
    """Rows as dicts (patient, lead, sms, age, noshow). Fields before the quoted Neighbourhood and after it are fixed-width,
    so the middle is ignored without parsing quotes."""
    rows = []
    in_data = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not in_data:
                if line.strip().lower() == "@data":
                    in_data = True
                continue
            line = line.strip()
            if not line or line.startswith("%"):
                continue
            parts = line.split(",")
            if len(parts) < 14:
                continue
            head, tail = parts[:6], parts[-7:]
            try:
                pid = head[0]
                lead = _days(head[4]) - _days(head[3])
                age = int(head[5])
                sms = int(tail[5])
                ns = tail[6].strip()
            except (ValueError, IndexError):
                continue
            if ns not in ("Yes", "No"):
                continue
            rows.append({"patient": pid, "lead": lead, "age": age, "sms": sms, "noshow": 1 if ns == "Yes" else 0})
    return rows


def keep(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["lead"] >= MIN_LEAD and 0 <= r["age"] <= 110]


def design(r: dict) -> list[float]:
    return [1.0, float(r["sms"]), math.log(r["lead"]), (r["age"] - AGE_CENTRE) / 10.0]


NAMES = ["intercept", "sms_received", "log_lead_days", "age_per_decade"]


def standardised_effect(beta: list[float], rows: list[dict]) -> float:
    """1 - mean p(SMS=1) / mean p(SMS=0), the other covariates as observed."""
    p0 = p1 = 0.0
    for r in rows:
        x = design(r)
        base = beta[0] + beta[2] * x[2] + beta[3] * x[3]
        p0 += S.sigmoid(base)
        p1 += S.sigmoid(base + beta[1])
    return 1.0 - p1 / p0


def fit_pooled(rows: list[dict]) -> dict:
    X = [design(r) for r in rows]
    y = [float(r["noshow"]) for r in rows]
    beta, cov, ll = S.logistic_newton(X, y)
    se = [math.sqrt(cov[i][i]) for i in range(4)]
    eff = standardised_effect(beta, rows)
    # delta method for the standardised effect: numerical gradient, covariance from the Newton Hessian
    g = []
    for i in range(4):
        h = 1e-5
        bp, bm = list(beta), list(beta)
        bp[i] += h
        bm[i] -= h
        g.append((standardised_effect(bp, rows) - standardised_effect(bm, rows)) / (2 * h))
    var = sum(g[i] * cov[i][j] * g[j] for i in range(4) for j in range(4))
    se_eff = math.sqrt(max(var, 0.0))
    return {"coef": dict(zip(NAMES, beta)), "se": dict(zip(NAMES, se)), "loglik": ll,
            "sms_odds_ratio": math.exp(beta[1]), "sms_odds_ratio_ci95": [math.exp(beta[1] - 1.96 * se[1]), math.exp(beta[1] + 1.96 * se[1])],
            "sms_relative_reduction": eff, "sms_relative_reduction_ci95": [eff - 1.96 * se_eff, eff + 1.96 * se_eff]}


def _marginal_nll(theta: list[float], groups: list[tuple[list[list[float]], list[int]]], nodes: list[float], weights: list[float]) -> float:
    beta, tau = theta[:4], math.exp(theta[4])
    if not -4 < theta[4] < 3:
        return 1e18
    total = 0.0
    us = [tau * z for z in nodes]
    lw = [math.log(w) for w in weights]
    for xs, ys in groups:
        etas = [sum(b * xv for b, xv in zip(beta, x)) for x in xs]
        sy = sum(ys[j] * etas[j] for j in range(len(ys)))
        sumy = sum(ys)
        terms = []
        for k, u in enumerate(us):
            sp = 0.0
            for e in etas:
                z = e + u
                sp += z + math.log1p(math.exp(-z)) if z > 0 else math.log1p(math.exp(z))
            terms.append(lw[k] + sy + sumy * u - sp)
        m = max(terms)
        total += m + math.log(sum(math.exp(t - m) for t in terms))
    return -total


def fit_mixed(rows: list[dict], start: list[float]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["patient"], []).append(r)
    groups = []
    for pid in sorted(by):
        rs = by[pid]
        if len(rs) >= 2:
            groups.append(([design(r) for r in rs], [r["noshow"] for r in rs]))
    nodes, weights = S.gauss_hermite(GH_NODES)
    x0 = list(start) + [math.log(0.7)]
    theta, nll = S.nelder_mead(lambda t: _marginal_nll(t, groups, nodes, weights), x0, [0.1, 0.1, 0.05, 0.05, 0.2], tol=1e-7, max_iter=400)
    tau = math.exp(theta[4])
    # no-show base rate of this subset, for reference
    n_obs = sum(len(g[1]) for g in groups)
    # profile likelihood interval for tau (others fixed at the optimum; a rough, stated approximation)
    base = nll
    prof = {}
    for f in (0.6, 0.8, 1.0, 1.25, 1.6):
        t2 = list(theta)
        t2[4] = math.log(tau * f)
        prof[f"{tau * f:.3f}"] = _marginal_nll(t2, groups, nodes, weights) - base
    return {"coef": dict(zip(NAMES, theta[:4])), "tau": tau, "tau_profile_delta_nll": prof, "neg_loglik": nll,
            "patients": len(groups), "appointments": n_obs, "sms_conditional_odds_ratio": math.exp(theta[1])}


def extract_noshow(path) -> dict:
    rows = parse_arff(path)
    k = keep(rows)
    return {"n_raw": len(rows), "rows": k}


def fit_noshow(ex: dict) -> dict:
    rows = ex["rows"]
    n = len(rows)
    ns = sum(r["noshow"] for r in rows)
    sms_rows = [r for r in rows if r["sms"]]
    pooled = fit_pooled(rows)
    out = {"n_raw": ex["n_raw"], "n_kept_lead_ge_3": n, "no_show_rate": ns / n,
           "no_show_rate_sms": sum(r["noshow"] for r in sms_rows) / len(sms_rows), "n_sms": len(sms_rows),
           "no_show_rate_no_sms": sum(r["noshow"] for r in rows if not r["sms"]) / (n - len(sms_rows)),
           "raw_relative_reduction": 1.0 - (sum(r["noshow"] for r in sms_rows) / len(sms_rows)) / (sum(r["noshow"] for r in rows if not r["sms"]) / (n - len(sms_rows))),
           "pooled": pooled}
    start = [pooled["coef"][k] for k in NAMES]
    out["mixed"] = fit_mixed(rows, start)
    out["patients_total"] = len({r["patient"] for r in rows})
    return out
