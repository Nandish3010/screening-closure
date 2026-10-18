"""Default plausible-range rules for the sensitivity sweep (ticket B14).

A swept parameter carries `range: [low, high]` and `range_basis` in its YAML leaf. When no published range exists the
range is made by one of the rules below and `range_basis` is the rule's exact label, so a test can recompute it:

  rate      a probability: value x 0.5 and x 1.5 (plus or minus 50 percent), clipped to [0, 1]
  duration  a time in days or working days: value x 0.5 and x 2 (a list is scaled element by element)
  scale     a dimensionless spread, shift or multiplier that is not a probability: value x 0.5 and x 2
  count     a whole number of attempts or days: value x 0.5 and x 2, rounded half up, at least 1, at most the engine limit
  share     a weight in a categorical mix: value x 0.5 and x 1.5; the mix is renormalised when read, so the other
            categories move in proportion

A range quoted from a source has a `range_basis` that starts with "stated range:".
"""

from __future__ import annotations

RATE = "default rule: rate, value x0.5 and x1.5, clipped to [0, 1]"
DURATION = "default rule: duration, value x0.5 and x2"
SCALE = "default rule: scale, value x0.5 and x2"
COUNT = "default rule: count, value x0.5 and x2, rounded half up, at least 1, at most the engine limit"
SHARE = "default rule: category weight, value x0.5 and x1.5, mix renormalised when read"
STATED_PREFIX = "stated range:"

RULE_BASES = {"rate": RATE, "duration": DURATION, "scale": SCALE, "count": COUNT, "share": SHARE}
# The engine pre-draws a fixed number of random numbers per case, so these counts cannot exceed their pool (params.py validate).
ENGINE_LIMITS = {"appointment.max_attempts": 4, "handoff.max_attempts": 5}


def _clean(x: float, like) -> float | int:
    """Round to 6 places; an integral result stays an int when the value itself is an int."""
    x = round(float(x), 6)
    return int(x) if isinstance(like, int) and x == int(x) else x


def _half_up(x: float) -> int:
    return int(x + 0.5)


def default_range(basis: str, key: str, value):
    """[low, high] that the rule named by `basis` gives for `value`, or None if `basis` is not a default rule."""
    if isinstance(value, list):
        lows, highs = zip(*(default_range(basis, key, v) for v in value))
        return [list(lows), list(highs)]
    if basis == RATE:
        return [_clean(max(0.0, value * 0.5), value), _clean(min(1.0, value * 1.5), value)]
    if basis in (DURATION, SCALE):
        return [_clean(value * 0.5, value), _clean(value * 2, value)]
    if basis == COUNT:
        return [max(1, _half_up(value * 0.5)), min(_half_up(value * 2), ENGINE_LIMITS.get(key, 10**9))]
    if basis == SHARE:
        return [_clean(value * 0.5, value), _clean(value * 1.5, value)]
    return None
