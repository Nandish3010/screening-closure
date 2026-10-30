"""M7-8: is the API pods' memory growth a plateau or a slow leak? Pure functions over a soak document's 60 s cgroup samples.

For each API pod, on its longest restart-free stretch, the anonymous memory (MiB) against time (hours) is
  * cut into one-hour slices, each with its least-squares slope (MiB/h): a leak keeps its slope hour after hour, a plateau's falls;
  * fitted twice, from the first 10 minutes on (the start-up transient is left out): a line  y = c + b t  and a saturating curve
    y = c + a (1 - exp(-t / tau))  (tau by grid search, c and a by least squares). The curve with the lower AIC wins when it is
    lower by more than 2; the saturating fit also gives the plateau c + a and the time constant tau.
No numpy: the grids are small.
"""

from __future__ import annotations

import math
from collections import defaultdict

SKIP_S = 600


def pod_series(doc: dict, prefix: str = "closure-api") -> dict[str, list[tuple[float, float]]]:
    """`{pod: [(hours since the stretch began, anon MiB)]}` on each pod's longest stretch without a restart."""
    rows: dict[str, list[dict]] = defaultdict(list)
    for s in doc["samples"]:
        for name, cg in (s.get("cgroup") or {}).items():
            if name.startswith(prefix) and "mem_anon" in cg:
                rc = ((s.get("pods") or {}).get(name) or {}).get("restart_count")
                rows[name].append({"t": s["t_epoch"], "rc": rc, "y": cg["mem_anon"] / 2**20})
    out = {}
    for name, rs in rows.items():
        stretches, cur = [], [rs[0]]
        for r in rs[1:]:
            if r["rc"] != cur[-1]["rc"]:
                stretches.append(cur)
                cur = []
            cur.append(r)
        stretches.append(cur)
        best = max(stretches, key=len)
        t0 = best[0]["t"]
        out[name] = [((r["t"] - t0) / 3600.0, r["y"]) for r in best]
    return out


def linfit(pts: list[tuple[float, float]]) -> tuple[float, float, float]:
    """(slope, intercept, sse) of y on t."""
    n = len(pts)
    if n < 3:
        return 0.0, pts[0][1] if pts else 0.0, 0.0
    mt = sum(t for t, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((t - mt) ** 2 for t, _ in pts)
    b = sum((t - mt) * (y - my) for t, y in pts) / sxx if sxx else 0.0
    c = my - b * mt
    return b, c, sum((y - (c + b * t)) ** 2 for t, y in pts)


def satfit(pts: list[tuple[float, float]]) -> dict | None:
    """y = c + a (1 - exp(-t / tau)): tau on a grid (minutes to a day), c and a by least squares. None when it cannot be fitted."""
    best = None
    t_end = pts[-1][0]
    for k in range(1, 400):
        tau = (k / 400) ** 2 * 24.0 + 0.02  # hours; denser at short tau
        xs = [(1 - math.exp(-t / tau)) for t, _ in pts]
        n = len(pts)
        mx, my = sum(xs) / n, sum(y for _, y in pts) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx < 1e-12:
            continue
        a = sum((x - mx) * (y - my) for x, (_, y) in zip(xs, pts)) / sxx
        c = my - a * mx
        sse = sum((y - (c + a * x)) ** 2 for x, (_, y) in zip(xs, pts))
        if a >= 0 and (best is None or sse < best["sse"]):
            best = {"tau_h": tau, "a": a, "c": c, "sse": sse, "plateau": c + a, "at_end": c + a * (1 - math.exp(-t_end / tau))}
    return best


def aic(sse: float, n: int, k: int) -> float:
    return n * math.log(max(sse, 1e-9) / n) + 2 * k


def analyse(doc: dict, prefix: str = "closure-api", skip_s: int = SKIP_S) -> dict:
    out = {}
    for pod, pts in pod_series(doc, prefix).items():
        hours = pts[-1][0]
        fit_pts = [(t, y) for t, y in pts if t * 3600 >= skip_s]
        slices = []
        for h in range(int(math.ceil(hours))):
            seg = [(t, y) for t, y in pts if h <= t < h + 1]
            if len(seg) >= 10:
                b, _, _ = linfit(seg)
                slices.append({"hour": h + 1, "samples": len(seg), "slope_mib_per_h": round(b, 2),
                               "start_mib": round(seg[0][1], 1), "end_mib": round(seg[-1][1], 1)})
        b, c, sse_l = linfit(fit_pts)
        sat = satfit(fit_pts)
        n = len(fit_pts)
        res = {"hours": round(hours, 2), "start_mib": round(pts[0][1], 1), "end_mib": round(pts[-1][1], 1),
               "max_mib": round(max(y for _, y in pts), 1), "slices": slices,
               "linear": {"slope_mib_per_h": round(b, 2), "intercept_mib": round(c, 1), "rmse_mib": round(math.sqrt(sse_l / n), 2), "aic": round(aic(sse_l, n, 2), 1)}}
        if sat:
            res["saturating"] = {"tau_h": round(sat["tau_h"], 2), "plateau_mib": round(sat["plateau"], 1), "rmse_mib": round(math.sqrt(sat["sse"] / n), 2),
                                 "aic": round(aic(sat["sse"], n, 3), 1), "fit_at_end_mib": round(sat["at_end"], 1)}
            d = res["linear"]["aic"] - res["saturating"]["aic"]
            res["aic_linear_minus_saturating"] = round(d, 1)
            res["preferred"] = "saturating" if d > 2 else ("linear" if d < -2 else "indistinguishable")
        else:
            res["preferred"] = "linear"
        last = slices[-1]["slope_mib_per_h"] if slices else None
        first = slices[0]["slope_mib_per_h"] if slices else None
        res["last_slice_slope_mib_per_h"], res["first_slice_slope_mib_per_h"] = last, first
        out[pod] = res
    return out
