"""The `empirical` status (ticket B18): validator rules in simulator/sim/params.py, and agreement of params.yaml with fitted.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SHA = "a" * 64


def parse(node):
    from simulator.sim.params import parse_params
    return parse_params({"k": node})


def test_empirical_needs_a_fit_script_and_a_sha256():
    from simulator.sim.params import ParamError
    ok = {"value": 1, "source": "s", "status": "empirical", "fit_script": "tools/calibration/x.py", "dataset_sha256": SHA}
    assert parse(ok)["k"].status == "empirical"
    for bad in ({**ok, "fit_script": ""}, {k: v for k, v in ok.items() if k != "fit_script"},
                {**ok, "dataset_sha256": "abc"}, {**ok, "dataset_sha256": "A" * 64}, {k: v for k, v in ok.items() if k != "dataset_sha256"}):
        with pytest.raises(ParamError):
            parse(bad)


def test_unknown_status_is_refused():
    from simulator.sim.params import ParamError
    with pytest.raises(ParamError, match="status"):
        parse({"value": 1, "source": "s", "status": "guessed"})


def test_every_status_in_the_shipped_parameter_files_is_allowed_and_empirical_leaves_are_complete():
    from simulator.sim.params import STATUSES, load_params
    p = load_params(ROOT / "simulator" / "params.yaml", ROOT / "simulator" / "params.conservative.yaml", warn=False)
    assert {r.status for r in p.rows()} <= set(STATUSES)
    raw = yaml.safe_load((ROOT / "simulator" / "params.yaml").read_text())
    def walk(n, path=""):
        if isinstance(n, dict) and "status" in n and "value" in n:
            yield path, n
        elif isinstance(n, dict):
            for k, v in n.items():
                yield from walk(v, f"{path}.{k}" if path else k)
    emp = {k: n for k, n in walk(raw) if n["status"] == "empirical"}
    assert len(emp) >= 8
    manifest = {e["sha256"] for e in json.loads((ROOT / "tools" / "calibration" / "manifest.json").read_text())["files"]}
    for k, n in emp.items():
        assert n["fit_script"].startswith("tools/calibration/") and (ROOT / n["fit_script"]).exists(), k
        assert n["dataset_sha256"] in manifest, k


def test_params_yaml_empirical_values_equal_the_committed_fits():
    import sys
    sys.path.insert(0, str(ROOT / "tools" / "calibration"))
    import calibrate as K
    fitted = json.loads(K.FITTED.read_text())
    assert K.params_diff(fitted["derived_params"]) == []
