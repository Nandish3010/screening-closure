"""Result priority (ADR-051): a tier derived from the values of a screening result by the rules of the case's pathway pack.

The comprehensive screening programme's loop starts by recording a result and then identifying and prioritising people who may
be at higher risk against agreed criteria. The criteria are DATA: the pack's `priority` section (`app.pathway.PriorityConfig`)
names the tiers (highest first), the result kinds and their fields, and the rules. This module holds no pathway name and no
number: it evaluates whatever rules the pack carries.

Where it runs. Once, at intake (`POST /cases` with `results`): the route derives the tier and records ONLY the tier and the id
of the rule that decided it on the genesis event (`priority`, `priority_rule`). The values are not stored: the event log is
immutable, and a measurement is health data that nothing downstream needs. The reason shown to staff is the rule's own wording,
looked up by id at read time. Re-deriving needs a new result, which is a new case.

What it does not do. It ranks and explains. It feeds no state, no transition, no SLA clock, no escalation and no closure, and a
patient never sees it. The SLA clock does not depend on the tier (ADR-051 says why).
"""

from __future__ import annotations

import operator
from dataclasses import dataclass

from app.pathway import PriorityConfig, PriorityRule

_OPS = {"gte": operator.ge, "gt": operator.gt, "lte": operator.le, "lt": operator.lt, "eq": operator.eq}
MAX_RESULTS = 8
# The rule id recorded when results were given and none matched: the tier is then the pack's default tier.
NO_RULE = "no_rule_matched"


class PriorityInputError(ValueError):
    """The submitted results do not fit the pack (an unknown kind or field, a non-number, too many results)."""


@dataclass(frozen=True)
class Derived:
    tier: str
    rule: str


def _check(config: PriorityConfig, results: list[dict]) -> None:
    if not isinstance(results, list) or not results or len(results) > MAX_RESULTS:
        raise PriorityInputError(f"results must list 1 to {MAX_RESULTS} results")
    for r in results:
        if not isinstance(r, dict) or set(r) != {"kind", "values"}:
            raise PriorityInputError("each result is {kind, values}")
        fields = config.results.get(r["kind"])
        if fields is None:
            raise PriorityInputError(f"result kind {str(r['kind'])[:40]!r} is not one this pathway ranks; known: {sorted(config.results)}")
        values = r["values"]
        if not isinstance(values, dict) or not values:
            raise PriorityInputError(f"result {r['kind']!r}: values must name at least one field")
        for name, value in values.items():
            if name not in fields:
                raise PriorityInputError(f"result {r['kind']!r}: unknown field {str(name)[:40]!r}; known: {list(fields)}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) > 1e6:
                raise PriorityInputError(f"result {r['kind']!r}: {name!r} must be a finite number")


def _matches(rule: PriorityRule, values: dict) -> bool:
    outcomes = [c.field in values and _OPS[c.op](values[c.field], c.value) for c in rule.conditions]
    return any(outcomes) if rule.mode == "any" else all(outcomes)


def derive(config: PriorityConfig, results: list[dict]) -> Derived:
    """The highest tier any rule gives to any result (ties go to the rule listed first); the pack's default tier, with the
    rule id `no_rule_matched`, when none matches. Raises `PriorityInputError` for results that do not fit the pack."""
    _check(config, results)
    best: tuple[int, int, PriorityRule] | None = None
    for r in results:
        for position, rule in enumerate(config.rules):
            if rule.kind == r["kind"] and _matches(rule, r["values"]):
                key = (config.rank(rule.tier), position)
                if best is None or key < best[:2]:
                    best = (key[0], key[1], rule)
    if best is None:
        return Derived(config.default_tier, NO_RULE)
    return Derived(best[2].tier, best[2].id)


def describe(config: PriorityConfig | None, tier: str | None, rule_id: str | None) -> dict | None:
    """The staff-facing priority of a case from what its genesis event recorded: the tier, its rank (0 is highest), the rule
    that decided it and the rule's wording in both languages and where its threshold comes from. None when the case has no
    recorded tier or the pack does not know it (a case is never shown a tier its pack cannot explain)."""
    if config is None or tier is None or tier not in config.tiers:
        return None
    rule = config.rule(rule_id)
    if rule is not None:
        reason, basis = dict(rule.reason), rule.basis
    elif rule_id == NO_RULE:
        reason = {"en": "No priority rule matched the result; the pathway's default tier applies",
                  "ar": "لم تنطبق أي قاعدة أولوية على النتيجة؛ يُطبَّق المستوى الافتراضي للمسار"}
        basis = "Pathway default tier"
    else:
        return None
    return {"tier": tier, "rank": config.rank(tier), "rule": rule_id, "reason": reason, "basis": basis}
