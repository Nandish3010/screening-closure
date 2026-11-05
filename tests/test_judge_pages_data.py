"""The three judge pages read web/src/judge/safetyData.json and pilotValue.json, generated from committed result files."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_pages_data", ROOT / "tools" / "judge" / "make_pages_data.py")
mp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mp)


def test_committed_files_match_a_fresh_generation():
    for path, text in mp.generate().items():
        assert path.read_text(encoding="utf-8") == text, f"run `make judge-pages-data` ({path.name})"


def test_safety_numbers_are_the_results_files_numbers():
    s = mp.build_safety()
    mlx = json.loads(mp.EVAL_MLX.read_text())
    gg = json.loads(mp.EVAL_GGUF.read_text())
    assert s["original"]["accuracy"] == mlx["accuracy"] and s["hosted"]["accuracy"] == gg["accuracy"]
    assert s["hosted"]["quantization"] == "Q8_0" and s["synthetic"] is True
    assert sum(s["dataset"]["test_by_label"].values()) == mlx["n"]["test"]
    rule = gg["groups"]["with_rule"]["by_dialect"]["ar_gulf"]
    assert s["hosted"]["by_dialect_with_rule"]["ar_gulf"]["auto_handled_n"] == rule["auto_handled_n"] == 0  # Gulf: always a person
    assert {f["group"] for f in s["fairness"]["triage_flags"]} == {"arabizi", "ar_gulf"}


def test_pilot_reference_is_the_cost_models_own_output_and_the_documented_scenario():
    p = mp.build_pilot()
    cm = mp._cost_model()
    _, spec = cm.load_inputs(mp.INPUTS)
    v = {k: s["value"] for k, s in spec.items()}
    shadow = cm.shadow_pilot(v)
    default = p["reference"][0]
    assert default["cases"] == shadow["cases"] == p["defaults"]["pilot_cases"]
    assert default["shadow"]["total"] == shadow["total"]
    assert round(shadow["total"]) == 105828 and round(shadow["cases"]) == 155
    # other case counts go through the same unmodified function
    for ref in p["reference"]:
        got = mp.shadow_cost(cm, v, ref["cases"])
        assert abs(got["cases"] - ref["cases"]) < 1e-9 and got == ref["shadow"]
    assert p["effort"]["all"]["reduction"]["labour_reduction"] < p["effort"]["target_reduction"]  # the 40% target is not met
    assert p["synthetic"] is True
