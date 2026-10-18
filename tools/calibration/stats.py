"""Small pure-Python numerics for the calibration fits (no numpy): normal cdf and quantile, Nelder-Mead, Gauss-Hermite,
Newton for logistic regression. Deterministic: no random numbers anywhere."""
from __future__ import annotations

import math
from typing import Callable, Sequence

SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / SQRT2)


def norm_ppf(p: float) -> float:
    """Inverse normal cdf (Acklam's rational approximation, refined by two Halley steps; error below 1e-12)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    for _ in range(2):
        e = norm_cdf(x) - p
        u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
        x -= u / (1 + x * u / 2)
    return x


def nelder_mead(f: Callable[[Sequence[float]], float], x0: Sequence[float], step: Sequence[float], tol: float = 1e-10, max_iter: int = 2000) -> tuple[list[float], float]:
    n = len(x0)
    pts = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] += step[i]
        pts.append(p)
    vals = [f(p) for p in pts]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol and max(abs(pts[-1][k] - pts[0][k]) for k in range(n)) < 1e-9:
            break
        cen = [sum(pts[i][k] for i in range(n)) / n for k in range(n)]
        xr = [cen[k] + (cen[k] - pts[-1][k]) for k in range(n)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [cen[k] + 2 * (cen[k] - pts[-1][k]) for k in range(n)]
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = [cen[k] + 0.5 * (pts[-1][k] - cen[k]) for k in range(n)] if fr >= vals[-1] else [cen[k] + 0.5 * (xr[k] - cen[k]) for k in range(n)]
            fc = f(xc)
            if fc < min(fr, vals[-1]):
                pts[-1], vals[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    pts[i] = [pts[0][k] + 0.5 * (pts[i][k] - pts[0][k]) for k in range(n)]
                    vals[i] = f(pts[i])
    i = min(range(n + 1), key=lambda j: vals[j])
    return pts[i], vals[i]


def gauss_hermite(n: int) -> tuple[list[float], list[float]]:
    """Nodes and weights of the n-point probabilists' Gauss-Hermite rule for E[g(Z)], Z ~ N(0,1) (weights sum to 1).
    Golub-Welsch by Jacobi eigenvalue iteration on the symmetric tridiagonal matrix (pure Python, n small)."""
    a = [[0.0] * n for _ in range(n)]
    for i in range(1, n):
        a[i][i - 1] = a[i - 1][i] = math.sqrt(i)
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(100):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-26:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-300:
                    continue
                th = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = (1 if th >= 0 else -1) / (abs(th) + math.sqrt(th * th + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    pairs = sorted((a[i][i], v[0][i] ** 2) for i in range(n))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-14:
            raise ValueError("singular matrix")
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def invert(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    cols = [solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def logistic_newton(X: list[list[float]], y: list[float], w: list[float] | None = None, offset: list[float] | None = None,
                    max_iter: int = 30, tol: float = 1e-10, beta0: list[float] | None = None) -> tuple[list[float], list[list[float]], float]:
    """Weighted logistic regression by Newton-Raphson. Returns (coefficients, covariance matrix, log-likelihood).
    `w` are case weights (frequency or posterior weights), `offset` an optional linear-predictor offset per row."""
    n, k = len(X), len(X[0])
    beta = list(beta0) if beta0 else [0.0] * k
    ll_old = -math.inf
    for _ in range(max_iter):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        ll = 0.0
        for i in range(n):
            xi = X[i]
            eta = sum(beta[j] * xi[j] for j in range(k)) + (offset[i] if offset else 0.0)
            p = sigmoid(eta)
            wi = w[i] if w else 1.0
            r = wi * (y[i] - p)
            v = wi * p * (1 - p)
            ll += wi * (y[i] * math.log(max(p, 1e-300)) + (1 - y[i]) * math.log(max(1 - p, 1e-300)))
            for a in range(k):
                g[a] += r * xi[a]
                va = v * xi[a]
                Ha = H[a]
                for b in range(a, k):
                    Ha[b] += va * xi[b]
        for a in range(k):
            for b in range(a):
                H[a][b] = H[b][a]
        step = solve(H, g)
        beta = [beta[j] + step[j] for j in range(k)]
        if abs(ll - ll_old) < tol and max(abs(s) for s in step) < 1e-8:
            ll_old = ll
            break
        ll_old = ll
    return beta, invert(H), ll_old
