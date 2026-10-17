"""Fitting and validating the risk model (D8, D10). Standard library only.

Plain L2-regularised logistic regression, fitted by Newton-Raphson from zero: the objective is
convex and the start is fixed, so the result depends on the data and nothing else (no random
initialisation, no shuffling). The data in turn depend only on the simulator seed, which is how the
whole of `model.json` is reproducible from a seed (T-D11).

Metrics for the held-out split: AUC (Mann-Whitney, ties at half), Brier score against the
base-rate predictor, a 10-bin reliability table over equal-width probability bins and the expected
calibration error (ECE, the count-weighted mean absolute gap between mean predicted and observed rate per bin).
`ece_noise_floor` simulates outcomes from the model's own probabilities to show how large ECE is
by chance alone at the held-out sample size.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Sequence

from . import features as F
from .model import Model, sigmoid

Row = tuple[F.CaseFeatures, int]
N_BINS = 10
DECIMALS = 8


def r8(x: float) -> float:
    return round(x, DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def sha256_of(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------------------------

def _skeleton(rows: Sequence[Row]) -> dict:
    """Model dict with zero coefficients and the encoding constants taken from `rows`."""
    blank = {
        "intercept": 0.0,
        "columns": [
            {"feature": f, "kind": k, "level": lv, "coef": 0.0, "center": 0.0, "scale": 1.0, "mean_enc": 0.0}
            for f, k, lv in F.COLUMNS
        ],
    }
    raw = Model(blank)
    z = [raw.encode(f) for f, _y in rows]
    n = len(z)
    cols = []
    for j, (f, k, lv) in enumerate(F.COLUMNS):
        mean = sum(r[j] for r in z) / n
        if k == "numeric":
            var = sum((r[j] - mean) ** 2 for r in z) / n
            center, scale, mean_enc = mean, (math.sqrt(var) or 1.0), 0.0
        else:
            center, scale, mean_enc = 0.0, 1.0, mean
        cols.append({"feature": f, "kind": k, "level": lv, "coef": 0.0,
                     "center": r8(center), "scale": r8(scale), "mean_enc": r8(mean_enc)})
    return {"intercept": 0.0, "columns": cols}


def _cholesky_solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                if s <= 0.0:
                    raise ValueError("Hessian is not positive definite: increase l2")
                L[i][i] = math.sqrt(s)
            else:
                L[i][j] = s / L[j][j]
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][k] * y[k] for k in range(i))) / L[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


def fit(rows: Sequence[Row], l2: float = 1.0, max_iter: int = 50, tol: float = 1e-10) -> tuple[dict, dict]:
    """Returns (model dict for `Model`, fit info). Minimises sum of log-loss + (l2 / 2) * |coef|^2
    (the intercept is not penalised)."""
    skeleton = _skeleton(rows)
    model = Model(skeleton)
    ncol = len(F.COLUMNS)
    d = ncol + 1  # the intercept is the last parameter
    X: list[list[tuple[int, float]]] = []
    ys: list[int] = []
    for f, y in rows:
        z = model.encode(f)
        nz = [(j, v) for j, v in enumerate(z) if v != 0.0]
        nz.append((ncol, 1.0))
        X.append(nz)
        ys.append(y)
    w = [0.0] * d
    iterations = 0
    for iterations in range(1, max_iter + 1):
        g = [0.0] * d
        H = [[0.0] * d for _ in range(d)]
        for nz, y in zip(X, ys):
            p = sigmoid(sum(w[j] * v for j, v in nz))
            resid, s = p - y, p * (1.0 - p)
            for a, (j, vj) in enumerate(nz):
                g[j] += resid * vj
                hj, svj = H[j], s * vj
                for k, vk in nz[a:]:
                    hj[k] += svj * vk
        for j in range(d):
            for k in range(j):
                H[j][k] = H[k][j]
        for j in range(ncol):
            g[j] += l2 * w[j]
            H[j][j] += l2
        H[ncol][ncol] += 1e-9  # the intercept is unpenalised; keeps the factorisation safe
        delta = _cholesky_solve(H, g)
        w = [wi - di for wi, di in zip(w, delta)]
        if max(abs(x) for x in delta) < tol:
            break
    for col, wj in zip(skeleton["columns"], w):
        col["coef"] = r8(wj)
    skeleton["intercept"] = r8(w[ncol])
    fitted = Model(skeleton)
    loss = -sum(math.log(max(p, 1e-15) if y else max(1 - p, 1e-15))
                for p, y in ((fitted.predict(f), y) for f, y in rows))
    return skeleton, {"iterations": iterations, "train_log_loss": r8(loss / len(rows)), "l2": l2}


# ---------------------------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------------------------

def auc(y: Sequence[int], p: Sequence[float]) -> float | None:
    """Probability that a random positive outranks a random negative (ties count half). None if a class is empty."""
    pos = sum(y)
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(len(p)), key=lambda i: p[i])
    rank_sum = 0.0
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and p[order[j + 1]] == p[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        rank_sum += avg_rank * sum(y[order[k]] for k in range(i, j + 1))
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def brier(y: Sequence[int], p: Sequence[float]) -> float:
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y)


def calibration_bins(y: Sequence[int], p: Sequence[float], n_bins: int = N_BINS) -> list[dict]:
    """Equal-width bins on [0, 1]; the last bin includes 1.0. Empty bins keep their row with nulls."""
    acc = [[0, 0.0, 0] for _ in range(n_bins)]
    for pi, yi in zip(p, y):
        b = min(int(pi * n_bins), n_bins - 1)
        acc[b][0] += 1
        acc[b][1] += pi
        acc[b][2] += yi
    out = []
    for b, (n, sp, sy) in enumerate(acc):
        out.append({
            "bin": b, "low": r8(b / n_bins), "high": r8((b + 1) / n_bins), "n": n,
            "mean_predicted": r8(sp / n) if n else None,
            "observed_rate": r8(sy / n) if n else None,
            "abs_gap": r8(abs(sy / n - sp / n)) if n else None,
        })
    return out


def ece_of(bins: Sequence[dict]) -> float:
    total = sum(b["n"] for b in bins)
    return sum(b["n"] * b["abs_gap"] for b in bins if b["n"]) / total if total else 0.0


def ece_noise_floor(p: Sequence[float], seed: int, draws: int = 200) -> dict:
    """ECE when outcomes are drawn from the model's own probabilities (a perfectly calibrated
    model): the mean and the 95th percentile over `draws` draws. An observed ECE inside this range
    is indistinguishable from perfect calibration at this sample size."""
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        yy = [1 if rng.random() < pi else 0 for pi in p]
        values.append(ece_of(calibration_bins(yy, p)))
    values.sort()
    return {"mean": r8(sum(values) / len(values)), "p95": r8(values[int(0.95 * (len(values) - 1))]), "draws": draws}


def evaluate(model: Model, rows: Sequence[Row], base_rate: float) -> dict:
    """All headline metrics on `rows` (the held-out split). `base_rate` is the training prevalence."""
    y = [yi for _f, yi in rows]
    p = [model.predict(f) for f, _y in rows]
    bins = calibration_bins(y, p)
    b = brier(y, p)
    b0 = brier(y, [base_rate] * len(y))
    return {
        "n": len(rows),
        "prevalence": r8(sum(y) / len(y)),
        "auc": r8(auc(y, p)),
        "brier": r8(b),
        "brier_base_rate": r8(b0),
        "brier_skill": r8(1.0 - b / b0),
        "ece": r8(ece_of(bins)),
        "calibration": bins,
    }


def group_metrics(model: Model, rows: Sequence[Row], base_rate: float, feature: str, levels: Sequence[str]) -> list[dict]:
    """AUC, ECE and Brier per level of `feature` on `rows`. A group with one outcome class has AUC null."""
    out = []
    for level in levels:
        sub = [(f, y) for f, y in rows if f.value_of(feature) == level]
        if not sub:
            out.append({"group": level, "n": 0, "prevalence": None, "auc": None, "ece": None, "brier": None})
            continue
        y = [yi for _f, yi in sub]
        p = [model.predict(f) for f, _y in sub]
        a = auc(y, p)
        out.append({
            "group": level, "n": len(sub), "prevalence": r8(sum(y) / len(y)),
            "auc": r8(a) if a is not None else None,
            "ece": r8(ece_of(calibration_bins(y, p))), "brier": r8(brier(y, p)),
            "mean_predicted": r8(sum(p) / len(p)),
        })
    return out
