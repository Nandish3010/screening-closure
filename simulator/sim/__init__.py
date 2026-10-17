"""Pure-Python discrete-event simulator of the positive-FIT -> colonoscopy -> closure pathway.

Importing this package puts the repo's `api/` directory first on sys.path so the simulator reuses
`app.pathway.load_pathway` and `app.state_machine.apply` (one source of truth for transitions).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_API = str(REPO_ROOT / "api")
if _API not in sys.path:
    sys.path.insert(0, _API)

DEFAULT_PARAMS = REPO_ROOT / "simulator" / "params.yaml"
DEFAULT_PATHWAY = REPO_ROOT / "config" / "pathways" / "colorectal_fit.yaml"
