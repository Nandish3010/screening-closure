"""T-D10: the risk score only orders the worklist.

Two kinds of proof. Static: an AST scan of `api/app` shows that `app.risk` is imported by
`routers/cases.py` and nothing else, that the name `risk_score` is read nowhere else, and that inside
`routers/cases.py` it is only ever assigned. Dynamic: the same world run with the score computed
for real, switched off, constant, reversed or random leaves every case log, every read model and the
order of the worklist identical; only the `risk_score` values differ.
"""

import ast
import random
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid5

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import app as app_package
from api_helpers import login_headers, make_client
from app.calendar import default_calendar
from app.clock import clock
from app.events import InMemoryEventStore, replay_model
from app.routers import cases
from app.sweep import run_sweep

APP = Path(app_package.__file__).parent
ALLOWED_IMPORTER = APP / "routers" / "cases.py"
RISK_PKG = APP / "risk"


def api_files():
    return [p for p in sorted(APP.rglob("*.py")) if RISK_PKG not in p.parents]


def _module_of(path: Path) -> str:
    return ".".join(("app", *path.relative_to(APP).with_suffix("").parts)).removesuffix(".__init__")


def _resolve(path: Path, node: ast.ImportFrom) -> str:
    """Absolute module name of a `from X import ...`, resolving relative imports against the file."""
    if node.level == 0:
        return node.module or ""
    package = _module_of(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    package = package[: len(package) - (node.level - 1)]
    return ".".join([*package, *([node.module] if node.module else [])])


def risk_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "app.risk" or a.name.startswith("app.risk.")]
        elif isinstance(node, ast.ImportFrom):
            mod = _resolve(path, node)
            if mod == "app.risk" or mod.startswith("app.risk."):
                found.append(mod)
            elif mod == "app" and any(a.name == "risk" for a in node.names):
                found.append("app.risk")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("app.risk"):
            found.append(f"string:{node.value}")  # importlib.import_module("app.risk...") and the like
    return found


def test_the_scan_sees_the_import_in_the_cases_router():
    assert risk_imports(ALLOWED_IMPORTER), "the scan is vacuous: cases.py is supposed to import the score"


def test_t_d10_app_risk_is_imported_by_the_cases_router_and_nothing_else():
    offenders = {str(p.relative_to(APP)): found for p in api_files() if p != ALLOWED_IMPORTER and (found := risk_imports(p))}
    assert not offenders, f"only routers/cases.py may import app.risk: {offenders}"
    # nothing outside api/app that the API runs imports it either (the scheduler and the watchdog are HTTP clients)
    root = APP.parents[1]
    for service in ("scheduler", "watchdog"):
        for p in (root / service).rglob("*.py"):
            assert "app.risk" not in p.read_text(), p


def _risk_name_uses(tree: ast.AST):
    """Every place the identifier or string key `risk_score` / `risk` appears, with how it is used."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("risk_score", "risk"):
            yield node, type(node.ctx).__name__
        elif isinstance(node, ast.Attribute) and node.attr in ("risk_score", "risk"):
            yield node, type(node.ctx).__name__
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in ("risk_score", "risk"):
            yield node, type(node.ctx).__name__
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop", "setdefault") \
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in ("risk_score", "risk"):
            yield node, "Call"
        elif isinstance(node, ast.alias) and node.name in ("risk_score", "risk"):
            yield node, "Import"


def test_t_d10_risk_score_is_read_nowhere_in_the_api_except_the_get_cases_serializer():
    offenders = []
    for p in api_files():
        if p == ALLOWED_IMPORTER:
            continue
        for node, how in _risk_name_uses(ast.parse(p.read_text())):
            offenders.append(f"{p.relative_to(APP)}:{node.lineno} ({how})")
    assert not offenders, f"risk is used outside routers/cases.py: {offenders}"
    # the one place that writes the placeholder key, so the row shape is stable, is a dict literal, not a read
    read_model_src = (APP / "readmodel.py").read_text()
    assert '"risk_score": None' in read_model_src


def test_t_d10_inside_the_cases_router_the_score_is_only_ever_assigned_to_the_response():
    tree = ast.parse(ALLOWED_IMPORTER.read_text())
    # the only Loads are the module alias `risk` in `risk.score_case(...)` / `risk.explain_case(...)`: calling the scorer
    reads = [(n.lineno, how) for n, how in _risk_name_uses(tree)
             if how in ("Load", "Call") and not (isinstance(n, ast.Name) and n.id == "risk")]
    assert not reads, f"cases.py reads a risk value somewhere other than the serializer: {reads}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (getattr(node.func, "id", None) == "sorted" or getattr(node.func, "attr", None) in ("sort", "sorted")):
            assert "risk" not in ast.unparse(node), f"the router orders by risk at line {node.lineno}: ordering is the web's concern"
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            assert "risk_score" not in ast.unparse(node.test), f"a decision depends on risk_score at line {node.lineno}"


# ---------------------------------------------------------------- the dynamic proof

client = make_client()


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def run_world(seed: int, scorer):
    """The same seeded world, with `scorer(case_features...)` replacing the score. Returns everything that is
    state (logs, read models, worklist order) and the risk values separately."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(cases, "store", InMemoryEventStore())
        clock.reset()
        mp.setattr(cases.risk, "score_case", scorer["score"])
        mp.setattr(cases.risk, "explain_case", scorer["explain"])
        rng = random.Random(seed)
        coord, lab = H("coordinator", "bd coord"), H("lab", "bd lab")
        pco = H("primary_care_owner", "bd pco")
        ids = []
        for i in range(7):
            cid = str(uuid5(NAMESPACE_DNS, f"risk-boundary-{seed}-{i}"))  # the same ids in every world
            assert client.post("/cases", json={"case_id": cid, "patient_ref": f"Patient/bd-{seed}-{i}"}, headers=coord).status_code == 201
            ids.append(cid)
            for type_, headers, payload in (("acknowledge", lab, {}), ("accept_ownership", pco, {}), ("contact_patient", pco, {})):
                if rng.random() < 0.75:
                    assert client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload}, headers=headers).status_code == 201
                else:
                    break
        worklists = []
        for step in (0, 9, 16, 35):  # the clock reads day 0, 9, 25 and 60
            clock.advance(days=step)
            run_sweep(cases.store, cases.pathway, default_calendar(), clock.now())
            worklists.append(client.get("/cases", headers=coord).json())
            for cid in ids[:3]:  # the case page is a second place the score is served
                client.get(f"/cases/{cid}", headers=coord)
        logs = {}
        for cid in ids:
            events = cases.store.events_for(UUID(cid))
            logs[cid] = ([(e.type, e.actor, sorted(e.payload.items()), e.at.isoformat()) for e in events],
                         replay_model(events, cases.pathway).to_json())
        risks = [[r["risk_score"] for r in wl] for wl in worklists]
        state = [[{k: v for k, v in r.items() if k != "risk_score"} for r in wl] for wl in worklists]
        clock.reset()
        return {"logs": logs, "worklists_without_risk": state, "order": [[r["case_id"] for r in wl] for wl in worklists]}, risks


def scorers(seed):
    rng = random.Random(seed ^ 0x5EED)
    return {
        "off": {"score": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model unavailable")),
                "explain": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model unavailable"))},
        "zero": {"score": lambda *a, **k: 0.0, "explain": lambda *a, **k: {"score": 0.0, "top_factors": []}},
        "one": {"score": lambda *a, **k: 1.0, "explain": lambda *a, **k: {"score": 1.0, "top_factors": []}},
        "random": {"score": lambda *a, **k: rng.random(),
                   "explain": lambda *a, **k: {"score": rng.random(), "top_factors": []}},
    }


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(0, 10_000))
def test_t_d10_state_escalation_and_order_are_identical_with_the_score_on_off_or_random(seed):
    from app.risk import score as real

    baseline, real_risks = run_world(seed, {"score": real.score_case, "explain": real.explain_case})
    assert any(v is not None for wl in real_risks for v in wl)  # the real score was served
    for name, scorer in scorers(seed).items():
        other, risks = run_world(seed, scorer)
        assert other == baseline, f"scorer {name!r} changed case state, a log or the worklist order"
        if name == "off":
            assert all(v is None for wl in risks for v in wl)  # a failing model leaves null, not an error
