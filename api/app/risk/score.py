"""Serving the risk score (D9): pure functions over a case's read model.

`score_case` returns the modelled probability that the case ends without a colonoscopy within 180
days of the result (0.0 once the colonoscopy is done or the case closed: nothing left to lose).
`explain_case` adds the three features that moved the score most. Neither function writes, reads a
clock, or touches the store: "now" and the events are passed in.

This module is imported by `app.routers.cases` and by nothing else (T-D10). The score orders a
worklist; it never gates, escalates or closes anything.
"""

from __future__ import annotations

from datetime import datetime

from . import features as F
from .model import Model, default_model

# The pathway the model was trained and validated on (the simulated bowel cohort). A case of any other pathway is not scored:
# a probability from another pathway's model would be a number nobody validated (docs/governance/second-pathway.md).
TRAINED_ON_PATHWAY = "colorectal_fit"

SCORE_DECIMALS = 4
TOP_FACTORS = 3


def score_features(f: F.CaseFeatures | None, model: Model | None = None) -> float:
    """Probability in [0, 1]; 0.0 for a case with nothing left to lose (`f` is None)."""
    if f is None:
        return 0.0
    return round((model or default_model()).predict(f), SCORE_DECIMALS)


def score_case(
    read_model, events, created_at: datetime, now: datetime, attributes: dict | None = None,
    model: Model | None = None,
) -> float:
    """Risk of loss to follow-up for one case, from its read model (`CaseModel`), its events, its
    genesis time and the simulation clock. See `features.features_from_case` for `attributes`."""
    return score_features(F.features_from_case(read_model, events, created_at, now, attributes), model)


def _value_shown(f: F.CaseFeatures, feature: str):
    v = f.value_of(feature)
    return round(v, 1) if isinstance(v, float) else v


def explain_features(f: F.CaseFeatures | None, model: Model | None = None) -> dict:
    """`{score, top_factors: [{feature, value, contribution}]}`. Contributions are in log-odds
    relative to an average training patient; a feature the case does not show (unknown) never appears."""
    m = model or default_model()
    if f is None:
        return {"score": 0.0, "top_factors": []}
    contributions = m.contributions(f)
    ranked = sorted(contributions.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    top = [
        {"feature": name, "value": _value_shown(f, name), "contribution": round(c, SCORE_DECIMALS)}
        for name, c in ranked
        if round(c, SCORE_DECIMALS) != 0.0
    ][:TOP_FACTORS]
    return {"score": round(m.predict(f), SCORE_DECIMALS), "top_factors": top}


def explain_case(
    read_model, events, created_at: datetime, now: datetime, attributes: dict | None = None,
    model: Model | None = None,
) -> dict:
    return explain_features(F.features_from_case(read_model, events, created_at, now, attributes), model)
