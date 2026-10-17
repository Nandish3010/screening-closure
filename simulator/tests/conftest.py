import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # `import generate_results`
REPO = HERE.parent.parent
for p in (REPO / "api", REPO):  # `import simulator.sim`, and the API's `app` package (worktree copy first)
    sys.path.insert(0, str(p))


@pytest.fixture(scope="session")
def ctx():
    from simulator.sim.runner import load_context

    return load_context(warn=False)


@pytest.fixture(scope="session")
def cal(ctx):
    return ctx.cal


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: regenerates a committed artefact from scratch (about 10 s); runs in `make test`")
