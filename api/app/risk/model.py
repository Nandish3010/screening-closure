"""The fitted model: columns, coefficients, and the arithmetic that turns features into a
probability and into per-feature contributions. Stdlib only.

`model.json` holds, per design-matrix column: `coef`, and the encoding constants `center` and
`scale` (numeric columns are standardised on the training split; indicator columns have centre 0
and scale 1) and `mean_enc`, the training mean of the encoded column. A missing feature is encoded
as `mean_enc`, so it contributes exactly zero relative to an average training patient.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from . import features as F

MODEL_PATH = Path(__file__).with_name("model.json")


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


@dataclass(frozen=True)
class Column:
    feature: str
    kind: str          # onehot | bool | numeric
    level: str | None
    coef: float
    center: float
    scale: float
    mean_enc: float

    @property
    def name(self) -> str:
        return F.column_name(self.feature, self.kind, self.level)


class Model:
    def __init__(self, raw: dict):
        self.raw = raw
        self.intercept: float = raw["intercept"]
        self.columns: tuple[Column, ...] = tuple(
            Column(c["feature"], c["kind"], c.get("level"), c["coef"], c["center"], c["scale"], c["mean_enc"])
            for c in raw["columns"]
        )
        banned = sorted({c.feature for c in self.columns} & set(F.EXCLUDED_FEATURES))
        if banned:
            raise ValueError(f"model.json uses excluded feature(s) {banned}: language and other proxies must not be inputs (D12)")
        names = tuple(c.name for c in self.columns)
        if names != F.COLUMN_NAMES:
            raise ValueError("model.json columns do not match app.risk.features.COLUMNS: retrain the model")
        self._levels: dict[str, frozenset[str]] = {}
        for c in self.columns:
            if c.kind == "onehot":
                self._levels[c.feature] = self._levels.get(c.feature, frozenset()) | {c.level}

    # -- encoding ---------------------------------------------------------------------------
    def _known(self, col: Column, value) -> bool:
        if value is None:
            return False
        if col.kind == "onehot":
            return value in self._levels[col.feature]
        return True

    def encode(self, f: F.CaseFeatures) -> list[float]:
        """Dense encoded vector, one entry per column; unknown values take the column's training mean."""
        z: list[float] = []
        for col in self.columns:
            value = f.value_of(col.feature)
            if not self._known(col, value):
                z.append(col.mean_enc)
            elif col.kind == "onehot":
                z.append(1.0 if value == col.level else 0.0)
            elif col.kind == "bool":
                z.append(1.0 if value else 0.0)
            else:
                days_or_count = F.cap_days(value) if col.feature == "days_since_result" else float(F.cap_count(value))
                z.append((days_or_count - col.center) / col.scale)
        return z

    # -- scoring ----------------------------------------------------------------------------
    def logit_of(self, z: list[float]) -> float:
        return self.intercept + sum(c.coef * v for c, v in zip(self.columns, z))

    def predict(self, f: F.CaseFeatures) -> float:
        return sigmoid(self.logit_of(self.encode(f)))

    def contributions(self, f: F.CaseFeatures) -> dict[str, float]:
        """Per feature: sum over its columns of coef * (encoded value - training mean), in log-odds.
        Zero for a feature that is unknown."""
        out: dict[str, float] = {}
        for col, v in zip(self.columns, self.encode(f)):
            out[col.feature] = out.get(col.feature, 0.0) + col.coef * (v - col.mean_enc)
        return out


def load_model(path: Path | None = None) -> Model:
    return Model(json.loads((path or MODEL_PATH).read_text()))


_default: Model | None = None


def default_model() -> Model:
    global _default
    if _default is None:
        _default = load_model()
    return _default
