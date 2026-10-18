"""The agency's configuration (`config/agency.yaml`) and limits (settings, A22)."""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from app.config import settings

@functools.lru_cache(maxsize=1)
def _load() -> dict:
    with Path(settings.agency_config_path).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data.get("providers"), list) or not all(isinstance(p, str) for p in data["providers"]):
        raise ValueError("config/agency.yaml: providers must be a list of names")
    return data


def providers() -> tuple[str, ...]:
    return tuple(_load()["providers"])


def call_script(language: str) -> tuple[str, ...]:
    return tuple(_load().get("call_script", {}).get(language, ()))


def every_sweeps() -> int:
    return max(0, int(settings.agency_every_sweeps))


def budget_seconds() -> float:
    return max(0.0, float(settings.agency_run_budget_seconds))


def max_tasks() -> int:
    return max(0, int(settings.agency_max_tasks))
