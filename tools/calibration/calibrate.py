"""Run every calibration fit from the local external files and write / check tools/calibration/fitted.json (ticket B18).

    python3 -I tools/calibration/calibrate.py fit      # fit all datasets, write fitted.json
    python3 -I tools/calibration/calibrate.py check    # re-fit, diff against the committed fitted.json and against the
                                                       # `empirical` leaves of simulator/params.yaml; exit 1 on any difference

The external files are UNTRUSTED: they are only parsed (zip/OLE2/text), never executed; this script runs in isolated mode.
Every file is checked against the committed manifest (tools/calibration/manifest.json) by sha256 before it is read.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as SRC  # noqa: E402
import fit_bands, fit_bcsp, fit_dm01, fit_noshow  # noqa: E402

FITTED = SRC.ROOT / "tools" / "calibration" / "fitted.json"
PARAMS = SRC.ROOT / "simulator" / "params.yaml"
REL_TOL = 1e-6


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest() -> dict:
    return {e["id"]: e for e in json.loads(SRC.MANIFEST_COMMITTED.read_text())["files"]}


def locate(man: dict, dataset: str) -> tuple[Path, str]:
    e = man[dataset]
    p = SRC.ROOT / e["path"]
    if not p.exists():
        raise SystemExit(f"{p} is missing: run `make calibrate-fetch`")
    got = sha256(p)
    if got != e["sha256"]:
        raise SystemExit(f"{p}: sha256 {got} does not match the manifest {e['sha256']} (file changed since download)")
    return p, got


def r8(x):
    """Round floats to 8 significant digits so the JSON is stable."""
    if isinstance(x, float):
        return float(f"{x:.8g}") if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: r8(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [r8(v) for v in x]
    return x


def derive_params(fits: dict) -> dict:
    """The simulator parameters that become status `empirical`: value, the script that fits it and the dataset it came from."""
    phs, bcsp, ns = fits["phs"], fits["nhs-bcsp"], fits["openml-noshow"]
    sc = phs["fit"]["scotland"]
    sha = {k: v["dataset_sha256"] for k, v in fits.items()}
    return {
        "time_to_colonoscopy_managed_ref_median_days": {"value": round(sc["fit_weeks_bands"]["median"], 1), "fit_script": "tools/calibration/fit_bands.py", "dataset": "phs", "dataset_sha256": sha["phs"]},
        "time_to_colonoscopy_managed_ref_iqr_days": {"value": [round(sc["fit_weeks_bands"]["p25"], 1), round(sc["fit_weeks_bands"]["p75"], 1)], "fit_script": "tools/calibration/fit_bands.py", "dataset": "phs", "dataset_sha256": sha["phs"]},
        "time_to_colonoscopy_lognormal_sigma": {"value": round(sc["fit_weeks_bands"]["sigma"], 2), "fit_script": "tools/calibration/fit_bands.py", "dataset": "phs", "dataset_sha256": sha["phs"]},
        "colonoscopy_uptake_after_positive_scotland": {"value": round(1.0 - sc["pi_never_in_window"], 3), "fit_script": "tools/calibration/fit_bands.py", "dataset": "phs", "dataset_sha256": sha["phs"]},
        "fit_positivity_rate_england_120ug": {"value": round(bcsp["fit"]["positivity"], 4), "fit_script": "tools/calibration/fit_bcsp.py", "dataset": "nhs-bcsp", "dataset_sha256": sha["nhs-bcsp"]},
        "colonoscopy_uptake_after_positive_england": {"value": round(bcsp["fit"]["diagnostic_uptake_after_positive"], 3), "fit_script": "tools/calibration/fit_bcsp.py", "dataset": "nhs-bcsp", "dataset_sha256": sha["nhs-bcsp"]},
        "sms_reminder_effect": {"value": round(ns["fit"]["pooled"]["sms_relative_reduction"], 3), "fit_script": "tools/calibration/fit_noshow.py", "dataset": "openml-noshow", "dataset_sha256": sha["openml-noshow"]},
        "appointment.responsiveness_noshow_shift": {"value": round(ns["fit"]["mixed"]["tau"], 2), "fit_script": "tools/calibration/fit_noshow.py", "dataset": "openml-noshow", "dataset_sha256": sha["openml-noshow"]},
    }


def run_fits() -> dict:
    man = manifest()
    out: dict = {}
    p, h = locate(man, "phs")
    out["phs"] = {"dataset_sha256": h, "fit": fit_bands.fit_phs(fit_bands.extract_phs(p))}
    p, h = locate(man, "nhs-bcsp")
    out["nhs-bcsp"] = {"dataset_sha256": h, "fit": fit_bcsp.fit_bcsp(fit_bcsp.extract_bcsp(p))}
    p, h = locate(man, "nhs-dm01")
    out["nhs-dm01"] = {"dataset_sha256": h, "fit": fit_dm01.fit_dm01(fit_dm01.extract_dm01(p))}
    p, h = locate(man, "openml-noshow")
    out["openml-noshow"] = {"dataset_sha256": h, "fit": fit_noshow.fit_noshow(fit_noshow.extract_noshow(p))}
    out["aihw-nbcsp-2024"] = {"status": "not downloaded (Cloudflare human verification); see tools/calibration/manifest.json not_downloaded"}
    out["derived_params"] = derive_params({k: v for k, v in out.items() if k in ("phs", "nhs-bcsp", "openml-noshow")})
    return r8(out)


def diff(a, b, path="") -> list[str]:
    msgs: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                msgs.append(f"{path}/{k}: present in only one side")
            else:
                msgs += diff(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            msgs.append(f"{path}: length {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                msgs += diff(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        if a is None or b is None or not math.isclose(a, b, rel_tol=REL_TOL, abs_tol=1e-9):
            msgs.append(f"{path}: {a} vs {b}")
    elif a != b:
        msgs.append(f"{path}: {a!r} vs {b!r}")
    return msgs


def _leaf_block(lines: list[str], key: str) -> str | None:
    """The indented block under `<last segment of key>:` (block-style leaf) in params.yaml, or None."""
    name = key.split(".")[-1]
    for i, line in enumerate(lines):
        m = re.fullmatch(rf"(\s*){re.escape(name)}:\s*(#.*)?", line)
        if m:
            ind = len(m.group(1))
            out = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= ind:
                    break
                out.append(nxt)
            return "\n".join(out)
    return None


def params_diff(derived: dict) -> list[str]:
    """Every derived parameter must be a block-style `empirical` leaf of params.yaml with the fitted value, its fit_script and the
    manifest sha256 of its dataset."""
    lines = PARAMS.read_text().splitlines()
    msgs: list[str] = []
    for key, d in derived.items():
        b = _leaf_block(lines, key)
        if b is None:
            msgs.append(f"params.yaml: {key} missing (block-style leaf expected)")
            continue
        if not re.search(r"^\s+status: empirical\s*$", b, re.M):
            msgs.append(f"params.yaml: {key} is not status empirical")
        if f"fit_script: {d['fit_script']}" not in b:
            msgs.append(f"params.yaml: {key} fit_script differs from {d['fit_script']}")
        if f"dataset_sha256: {d['dataset_sha256']}" not in b:
            msgs.append(f"params.yaml: {key} dataset_sha256 differs from the manifest")
        vm = re.search(r"^\s+value:\s*(\[[^\]]*\]|[-0-9.eE]+)\s*$", b, re.M)
        if not vm:
            msgs.append(f"params.yaml: {key} has no readable value")
            continue
        got = json.loads(vm.group(1))
        if diff(r8(d["value"]), r8(got)):
            msgs.append(f"params.yaml: {key} value {got} != fitted {d['value']}")
    return msgs


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "fit"
    fits = run_fits()
    text = json.dumps(fits, indent=1, sort_keys=True) + "\n"
    if cmd == "fit":
        FITTED.write_text(text)
        print(f"wrote {FITTED.relative_to(SRC.ROOT)}")
        for k, v in fits["derived_params"].items():
            print(f"  {k:48s} {v['value']}")
        return 0
    if cmd == "check":
        old = json.loads(FITTED.read_text())
        msgs = diff(old, fits) + params_diff(fits["derived_params"])
        if msgs:
            print("calibrate-check: FAIL")
            for m in msgs[:40]:
                print("  ", m)
            return 1
        print(f"calibrate-check: PASS, fits reproduce tools/calibration/fitted.json and the empirical leaves of simulator/params.yaml ({len(fits['derived_params'])} parameters)")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
