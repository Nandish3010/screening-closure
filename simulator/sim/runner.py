"""Glue: load parameters + pathway into a Context, build a cohort and run an arm."""

from __future__ import annotations

import hashlib
import subprocess
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.calendar import load_calendar
from app.pathway import Pathway, load_pathway

from . import DEFAULT_PARAMS, DEFAULT_PATHWAY, REPO_ROOT
from .calendar import WorkingCalendar
from .engine import Engine, RunResult
from .metrics import AbstentionHook, compute_metrics
from .params import AssumedParamsWarning, ModelParams, Params, load_params
from .patients import Patient, make_cohort


@dataclass
class Context:
    params: Params
    mp: ModelParams
    pathway: Pathway
    pathway_path: Path
    cal: WorkingCalendar

    def pathway_hash(self) -> str:
        return hashlib.sha256(self.pathway_path.read_bytes()).hexdigest()


def load_context(params_paths: list[str | Path] | None = None, pathway_path: str | Path | None = None,
                 warn: bool = True) -> Context:
    paths = list(params_paths) if params_paths else [DEFAULT_PARAMS]
    with warnings.catch_warnings():
        if not warn:
            warnings.simplefilter("ignore", AssumedParamsWarning)
        params = load_params(*paths)
    return context_from_params(params, pathway_path)


def context_from_params(params: Params, pathway_path: str | Path | None = None) -> Context:
    """A Context from an already loaded (possibly modified) parameter store; the sensitivity sweep uses this."""
    mp = ModelParams.from_params(params)
    pw_path = Path(pathway_path) if pathway_path else DEFAULT_PATHWAY
    pathway = load_pathway(pw_path)
    # M7-1: the API's calendar (config/calendar.yaml: weekend, holidays, time zone), never one of the simulator's own. The
    # simulator's parameters still state the working week (they are cited in the evidence register); they must agree.
    api = load_calendar(REPO_ROOT / "config" / "calendar.yaml")
    if frozenset(mp.weekend_weekdays) != api.weekend or frozenset(date.fromisoformat(h) for h in mp.holidays) != api.holidays:
        raise ValueError("working_week.weekend and calendar.holidays must equal config/calendar.yaml (the API's calendar): "
                         f"weekend {sorted(mp.weekend_weekdays)} vs {sorted(api.weekend)}, holidays {list(mp.holidays)}")
    cal = WorkingCalendar.from_api(api, date.fromisoformat(mp.start_date))
    if not cal.is_working_day(cal.start):
        raise ValueError(f"simulation.start_date {mp.start_date} must be a working day")
    return Context(params, mp, pathway, pw_path, cal)


def make_patients(ctx: Context, cases: int, seed: int) -> list[Patient]:
    return make_cohort(cases, seed, ctx.mp, ctx.cal)


def run_arm(ctx: Context, arm: str, cases: int, days: int, seed: int,
            patients: list[Patient] | None = None) -> RunResult:
    pts = patients if patients is not None else make_patients(ctx, cases, seed)
    engine = Engine(arm, ctx.mp, ctx.pathway, ctx.cal, days, seed)
    result = engine.run(pts)
    return result


def metrics_of(ctx: Context, result: RunResult, abstention_hook: AbstentionHook | None = None) -> dict:
    mp = ctx.mp
    return compute_metrics(
        result.records,
        cal=ctx.cal,
        ltfu_idle_days=mp.ltfu_idle_days,
        escalation_window_wd=mp.escalation_acted_on_window_working_days,
        min_group_size=mp.min_group_size,
        delay_harm_days=mp.delay_harm_threshold_days,
        abstention_hook=abstention_hook,
    )


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
