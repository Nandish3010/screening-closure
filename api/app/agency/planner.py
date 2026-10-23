"""Planning for the agency's department heads and the supervisor's rationale (A22, ADR-033).

A head's rules produce its candidate orders (`DepartmentPlan`). The planner may then do two things, and only these:
put the orders in priority order, and write a one-paragraph rationale. It cannot add an order, drop one, change an
order's agent type, case or inputs, or name a tool: the head executes exactly its own candidates, in the order the
planner gives (any candidate the planner leaves out is appended at the end). So a model, at worst, puts the work in
a poor order and writes a poor sentence; it never decides what is done.

* `TemplatePlanner` (default, offline, deterministic): keeps the rule order and writes a fixed rationale.
* `ModelPlanner`: the configured drafting provider (`LLM_PROVIDER`, `azure_openai` or `jais`) behind the same
  record/replay cache as drafting (`LLM_CACHE_MODE`, files under `<cache dir>/agency/<provider>/<prompt hash>.json`)
  and the same network switch (`LLM_NETWORK_ENABLED`). Whatever comes back is parsed into `ModelPlanReply` (strict)
  and checked; a miss, an error or an invalid reply falls back to the template plan with `fallback_used = true` and
  an abstention reason, exactly like a draft.

The prompt carries ids and labels only (agent type, case id, state, overdue, escalation level, reason codes): the
facts an agent holds have no text in them (`app.agency.facts`), so a patient's reply or a note cannot reach it.
Every plan is recorded on its ledger row with `prompt_hash`, `provider`, `model_id` and `fallback_used`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.ai.provider import TEMPLATE_PROVIDER, configured_provider_name

log = logging.getLogger("app.agency.planner")

TEMPLATE_MODEL_ID = "agency-template-planner-v1"
MAX_ORDERS = 400
RATIONALE_MAX = 400

PLAN_SYSTEM_PROMPT = (
    "You help a screening programme's operations team put a list of preparatory work items in priority order. "
    "You never decide care, never change a patient's case, and never add or remove a work item. "
    "Reply with JSON only: {\"order\": [indices of the items, most urgent first], \"rationale\": \"one or two "
    "plain sentences for the team, no names, no numbers longer than four digits, no links\"}."
)

_LINKY = re.compile(r"https?://|www\.|@|\b[a-z0-9-]+\.(com|net|org|ae|io)\b", re.I)
_LONG_DIGITS = re.compile(r"\d[\d\s\-./]{5,}\d")
# control (line breaks, NUL), format (bidi overrides, zero-width), surrogate and line/paragraph separator characters
_UNSAFE_CHARS = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class TaskOrder(BaseModel):
    """One executioner task a head wants: which agent, which case (or none), and its explicit inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_type: str = Field(pattern=r"^[a-z][a-z_]{1,48}$")
    case_id: str | None = None
    key: str = Field(default="", max_length=40, pattern=r"^[a-z0-9_:-]*$")
    reason: str = Field(max_length=60, pattern=r"^[a-z][a-z0-9_]*$")  # a code, never prose
    inputs: dict = Field(default_factory=dict)


class Deferred(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    reason: str = Field(max_length=60, pattern=r"^[a-z][a-z0-9_]*$")


class DepartmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orders: list[TaskOrder] = Field(default_factory=list, max_length=MAX_ORDERS)
    deferred: list[Deferred] = Field(default_factory=list, max_length=MAX_ORDERS)
    rationale: str = Field(default="", max_length=RATIONALE_MAX)


class ModelPlanReply(BaseModel):
    """What a model may answer. Anything else is invalid and the template plan is used."""

    model_config = ConfigDict(extra="forbid", strict=True)

    order: list[int] = Field(max_length=MAX_ORDERS)
    rationale: str = Field(min_length=1, max_length=RATIONALE_MAX)

    @field_validator("rationale")
    @classmethod
    def _plain(cls, value: str) -> str:
        if any(unicodedata.category(c) in _UNSAFE_CHARS for c in value.strip()) or _LINKY.search(value) or _LONG_DIGITS.search(value) or "{" in value:
            raise ValueError("the rationale must be plain sentences: no links, addresses or long numbers")
        return value.strip()


@dataclass(frozen=True)
class Provenance:
    prompt_hash: str
    provider: str
    model_id: str
    fallback_used: bool
    abstention_reason: str | None = None

    def to_json(self) -> dict:
        return {"prompt_hash": self.prompt_hash, "provider": self.provider, "model_id": self.model_id,
                "fallback_used": self.fallback_used, "abstention_reason": self.abstention_reason}


def plan_prompt(department: str, plan: DepartmentPlan) -> str:
    """The user message: the department and its candidate items, as ids and codes. Deterministic."""
    items = [{"i": i, "agent_type": o.agent_type, "case_id": o.case_id, "reason": o.reason,
              "labels": {k: v for k, v in sorted(o.inputs.items()) if k in _LABEL_KEYS}}
             for i, o in enumerate(plan.orders)]
    return json.dumps({"department": department, "items": items}, sort_keys=True, separators=(",", ":"))


# Which input keys are labels a planner may see (all enums, ids, counts or ISO times; never text).
_LABEL_KEYS = frozenset({"state", "overdue", "escalation_level", "due_at", "messages_sent", "contact_attempts",
                         "pending_handoff_to", "appointment_at", "day"})


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n---\n{user}".encode()).hexdigest()


def _template_rationale(department: str, plan: DepartmentPlan) -> str:
    if plan.rationale:
        return plan.rationale
    counts: dict[str, int] = {}
    for o in plan.orders:
        counts[o.agent_type] = counts.get(o.agent_type, 0) + 1
    parts = [f"{n} {t}" for t, n in sorted(counts.items())]
    deferred = f"; {len(plan.deferred)} deferred" if plan.deferred else ""
    return (f"{department}: " + (", ".join(parts) if parts else "nothing to prepare") + deferred + ".")[:RATIONALE_MAX]


class TemplatePlanner:
    name = TEMPLATE_PROVIDER
    model_id = TEMPLATE_MODEL_ID

    def plan(self, department: str, plan: DepartmentPlan) -> tuple[DepartmentPlan, Provenance]:
        user = plan_prompt(department, plan)
        out = plan.model_copy(update={"rationale": _template_rationale(department, plan)})
        return out, Provenance(prompt_hash(PLAN_SYSTEM_PROMPT, user), self.name, self.model_id, False)


def _ordered(plan: DepartmentPlan, reply: ModelPlanReply) -> list:
    n = len(plan.orders)
    seen: list[int] = []
    for i in reply.order:
        if not 0 <= i < n:
            raise ValueError(f"order names item {i}, the plan has {n}")
        if i in seen:
            raise ValueError(f"order names item {i} twice")
        seen.append(i)
    rest = [i for i in range(n) if i not in seen]  # a planner cannot drop work: anything left out goes last
    return [plan.orders[i] for i in (*seen, *rest)]


class ModelPlanner:
    """A model-backed planner over the drafting provider's adapter and cache. Never raises for a model problem."""

    def __init__(self, provider_name: str, adapter=None) -> None:
        self.name = provider_name
        self._adapter = adapter

    def _adapter_for(self):
        if self._adapter is not None:
            return self._adapter
        from app.ai import adapters

        return {"azure_openai": adapters.AzureOpenAIProvider, "jais": adapters.JaisProvider}[self.name]()

    @property
    def model_id(self) -> str:
        try:
            return self._adapter_for().model_id
        except Exception:  # noqa: BLE001
            return "unconfigured"

    def _ask(self, user: str, digest: str) -> tuple[str, str]:
        """(text, model_id) from the cache or the model. Raises on a miss or any provider problem."""
        from app.ai import cache

        directory = cache.cache_dir() / "agency"
        mode = cache.cache_mode()
        if mode == "replay":
            entry = cache.read_entry(self.name, digest, directory)
            if entry is None:
                raise LookupError("cache_miss")
            return entry.text, entry.model_id
        adapter = self._adapter_for()
        text = adapter.complete(PLAN_SYSTEM_PROMPT, user, max_tokens=800)
        if mode == "record":
            cache.write_entry(cache.CacheEntry(digest, self.name, adapter.model_id, "en", text,
                                               f"{PLAN_SYSTEM_PROMPT}\n---\n{user}"), directory)
        return text, adapter.model_id

    def plan(self, department: str, plan: DepartmentPlan) -> tuple[DepartmentPlan, Provenance]:
        user = plan_prompt(department, plan)
        digest = prompt_hash(PLAN_SYSTEM_PROMPT, user)
        try:
            text, model_id = self._ask(user, digest)
        except LookupError:
            return self._fallback(department, plan, digest, "cache_miss")
        except Exception as exc:  # noqa: BLE001 - unavailable, timeout, misconfigured: the template plan
            return self._fallback(department, plan, digest, f"provider_error:{type(exc).__name__}")
        try:
            reply = ModelPlanReply.model_validate(json.loads(text))
            orders = _ordered(plan, reply)
        except (ValueError, ValidationError, TypeError) as exc:
            log.info("agency plan from %s rejected: %s", self.name, str(exc).splitlines()[0])
            return self._fallback(department, plan, digest, "invalid_result")
        out = plan.model_copy(update={"orders": orders, "rationale": reply.rationale})
        return out, Provenance(digest, self.name, model_id, False)

    def _fallback(self, department, plan, digest, reason) -> tuple[DepartmentPlan, Provenance]:
        out = plan.model_copy(update={"rationale": _template_rationale(department, plan)})
        return out, Provenance(digest, TEMPLATE_PROVIDER, TEMPLATE_MODEL_ID, True, reason)


def get_planner():
    """`LLM_PROVIDER=template` (the default) plans with rules only; a model provider plans through its cache."""
    name = configured_provider_name()
    if name == TEMPLATE_PROVIDER:
        return TemplatePlanner()
    if name in ("azure_openai", "jais"):
        return ModelPlanner(name)
    return TemplatePlanner()  # an unknown provider is refused at drafting; planning never depends on it
