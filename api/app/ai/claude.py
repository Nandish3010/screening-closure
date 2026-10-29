"""Claude as a model provider (A35, ADR-043): patient-message drafting (Haiku) and the agency planner (Sonnet).

Everything here sits behind the same gates as the other providers:

* The network is OFF unless `LLM_NETWORK_ENABLED=true`. In `replay` cache mode (the default, and the demo's) this module is
  never reached: `app.ai.cache.CachingProvider` answers from the recorded files, and the SDK is not even imported.
* A request carries `SYSTEM_PROMPT` and `build_prompt(req)` only (drafting), or the planner's ids and labels (planning). No
  patient name, contact detail, identifier, result or free text exists in either (ai-boundary.md sections 2 and 8).
* A spend cap is enforced in this process before every call (`UsageLedger`): `LLM_DAILY_TOKEN_BUDGET` for the UTC day,
  `LLM_MAX_TOKENS_PER_CALL` for one call's output and `LLM_MAX_INPUT_TOKENS_PER_CALL` for its (estimated) prompt. A call that would pass either is refused with `BudgetExhausted` and the caller uses
  the template (abstention reason `budget_exhausted`). The ledger is a small SQLite file (`data/llm_usage.sqlite`,
  `LLM_USAGE_DB` overrides) so the count survives restarts and is shared by processes. It holds counts, never text.
* Any SDK error, a refusal, a cut-off answer or an empty answer becomes `ProviderUnavailable` / `ProviderResponseError`;
  neither reaches the API caller, who gets the template draft. Error messages carry the exception class name only.
* The key is read by the SDK from `ANTHROPIC_API_KEY` in the environment (a key not scoped to a workspace also needs
  `ANTHROPIC_WORKSPACE_ID`, sent as the `anthropic-workspace-id` header). It is never read from a file here, logged, or put in
  an error message or a cache file.

Models (the exact ids from the claude-api skill, no date suffix): drafting `claude-haiku-5-5`, planning `claude-sonnet-5-5`.
`ANTHROPIC_DRAFT_MODEL` / `ANTHROPIC_PLAN_MODEL` override them. Both run at `effort: low` (short outputs, no sampling
parameters: Haiku 5.5 and Sonnet 5.5 reject non-default ones). The planner asks for JSON through `output_config.format`
(a JSON schema), not forced tool use, which Sonnet 5.5 rejects; the reply is then parsed by the existing strict pydantic
`ModelPlanReply` and contained by `app.agency.planner.contained`. The system prompts are short (under the 512-token minimum
for caching), so `cache_control` is set but will not take effect until a prompt grows; cache token counts are still recorded.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.ai.adapters import ProviderResponseError, network_enabled
from app.ai.provider import (
    SYSTEM_PROMPT,
    BudgetExhausted,
    DraftRequest,
    DraftResult,
    ProviderUnavailable,
    build_prompt,
    prompt_hash,
)

log = logging.getLogger("app.ai.claude")

PROVIDER_NAME = "anthropic"
DEFAULT_DRAFT_MODEL = "claude-haiku-5-5"
DEFAULT_PLAN_MODEL = "claude-sonnet-5-5"
DEFAULT_DAILY_BUDGET = 200_000
DEFAULT_CALL_OUTPUT_CAP = 2048  # output tokens (max_tokens) of one call
DEFAULT_CALL_INPUT_CAP = 30_000  # estimated prompt tokens of one call
DEFAULT_TIMEOUT_SECONDS = 30.0
_REPO_ROOT = Path(__file__).resolve().parents[3]

# The planner's answer, as a JSON schema for output_config.format. The bounds the pydantic model enforces (list length,
# rationale length and shape) are checked again after the call; a schema keeps the shape, not the content.
PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "integer"}},
        "rationale": {"type": "string"},
    },
    "required": ["order", "rationale"],
    "additionalProperties": False,
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _int_env(name: str, default: int) -> int:
    try:
        value = int(_env(name))
    except ValueError:
        return default
    return value if value >= 0 else default


def draft_model() -> str:
    return _env("ANTHROPIC_DRAFT_MODEL") or DEFAULT_DRAFT_MODEL


def plan_model() -> str:
    return _env("ANTHROPIC_PLAN_MODEL") or DEFAULT_PLAN_MODEL


def daily_token_budget() -> int:
    return _int_env("LLM_DAILY_TOKEN_BUDGET", DEFAULT_DAILY_BUDGET)


def max_tokens_per_call() -> int:
    return _int_env("LLM_MAX_TOKENS_PER_CALL", DEFAULT_CALL_OUTPUT_CAP)


def workspace_headers() -> dict[str, str]:
    """A key that is not scoped to a workspace needs `anthropic-workspace-id`; `ANTHROPIC_WORKSPACE_ID` supplies it."""
    value = _env("ANTHROPIC_WORKSPACE_ID")
    return {"anthropic-workspace-id": value} if value else {}


def max_input_tokens_per_call() -> int:
    return _int_env("LLM_MAX_INPUT_TOKENS_PER_CALL", DEFAULT_CALL_INPUT_CAP)


def _timeout() -> float:
    try:
        value = float(_env("LLM_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if 0 < value <= 300 else DEFAULT_TIMEOUT_SECONDS


def usage_db_path() -> Path:
    override = _env("LLM_USAGE_DB")
    return Path(override) if override else _REPO_ROOT / "data" / "llm_usage.sqlite"


def estimate_input_tokens(*texts: str) -> int:
    """A rough, slightly high estimate (one token per three characters) used only to hold budget and to refuse an
    oversized prompt before the call. The real count comes back in `usage` and is what the ledger keeps."""
    return sum(len(t) for t in texts) // 3 + 16


def usage_counts(usage) -> dict:
    """Counts only, from an SDK `Usage` object (or None)."""
    def n(name: str) -> int:
        value = getattr(usage, name, 0)
        return value if isinstance(value, int) else 0

    return {
        "input_tokens": n("input_tokens"),
        "output_tokens": n("output_tokens"),
        "cache_read_input_tokens": n("cache_read_input_tokens"),
        "cache_creation_input_tokens": n("cache_creation_input_tokens"),
    }


def total_tokens(counts: dict) -> int:
    return sum(int(counts.get(k) or 0) for k in
               ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


class UsageLedger:
    """Persistent token counter. One row per call (counts, model, purpose, prompt hash; no text). A row is inserted as a
    reservation before the call and settled with the real counts after it, inside one SQLite write lock, so two processes
    cannot both spend the last of the day's budget."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else usage_db_path()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, "
            "ts TEXT NOT NULL, purpose TEXT NOT NULL, model TEXT NOT NULL, prompt_hash TEXT NOT NULL, "
            "reserved INTEGER NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, "
            "cache_read_input_tokens INTEGER NOT NULL DEFAULT 0, cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0, "
            "status TEXT NOT NULL)"
        )
        return conn

    @staticmethod
    def _day(now: datetime | None) -> str:
        return (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()  # wall-clock-ok: the token budget is a real-time spend cap, not case logic

    def spent(self, now: datetime | None = None) -> int:
        """Tokens used today (settled calls) plus tokens held by calls still in flight."""
        with closing(self._connect()) as conn:
            return self._spent(conn, self._day(now))

    @staticmethod
    def _spent(conn: sqlite3.Connection, day: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN status = 'pending' THEN reserved ELSE input_tokens + output_tokens + "
            "cache_read_input_tokens + cache_creation_input_tokens END), 0) FROM llm_usage WHERE day = ?", (day,)
        ).fetchone()
        return int(row[0])

    def reserve(self, purpose: str, model: str, digest: str, input_estimate: int, max_tokens: int,
                now: datetime | None = None) -> int:
        """Refuse (BudgetExhausted) or hold `input_estimate + max_tokens` tokens; returns the row id to settle."""
        cap = max_tokens_per_call()
        if max_tokens > cap:
            raise BudgetExhausted(f"max_tokens {max_tokens} is over LLM_MAX_TOKENS_PER_CALL {cap}")
        if input_estimate > max_input_tokens_per_call():
            raise BudgetExhausted(f"prompt of about {input_estimate} tokens is over LLM_MAX_INPUT_TOKENS_PER_CALL")
        want = input_estimate + max_tokens
        budget = daily_token_budget()
        day = self._day(now)
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")  # wall-clock-ok: spend ledger stamp
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                used = self._spent(conn, day)
                if used + want > budget:
                    conn.execute("ROLLBACK")
                    raise BudgetExhausted(f"daily token budget {budget} would be exceeded ({used} used, {want} needed)")
                cur = conn.execute(
                    "INSERT INTO llm_usage (day, ts, purpose, model, prompt_hash, reserved, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending')", (day, stamp, purpose, model, digest, want))
                conn.execute("COMMIT")
                return int(cur.lastrowid)
            except BudgetExhausted:
                raise
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def settle(self, row_id: int, counts: dict, status: str = "ok") -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE llm_usage SET input_tokens = ?, output_tokens = ?, cache_read_input_tokens = ?, "
                "cache_creation_input_tokens = ?, status = ? WHERE id = ?",
                (int(counts.get("input_tokens") or 0), int(counts.get("output_tokens") or 0),
                 int(counts.get("cache_read_input_tokens") or 0), int(counts.get("cache_creation_input_tokens") or 0),
                 status, row_id))

    def rows(self) -> list[dict]:
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM llm_usage ORDER BY id")]


class _ClaudeCaller:
    """The shared call: gate, reserve, call, settle, extract text. `client` is an `anthropic.Anthropic` (tests pass a fake)."""

    def __init__(self, client=None, ledger: UsageLedger | None = None) -> None:
        self._client = client
        self._ledger = ledger
        self.last_usage: dict | None = None

    @property
    def ledger(self) -> UsageLedger:
        if self._ledger is None:
            self._ledger = UsageLedger()
        return self._ledger

    def _sdk(self):
        if self._client is not None:
            return self._client
        if not _env("ANTHROPIC_API_KEY"):
            raise ProviderUnavailable("anthropic is not configured (ANTHROPIC_API_KEY)")
        import anthropic  # lazy: replay mode and the default configuration never need it

        self._client = anthropic.Anthropic(timeout=_timeout(), max_retries=1, default_headers=workspace_headers())
        return self._client

    def call(self, *, purpose: str, model: str, system: str, user: str, digest: str, max_tokens: int,
             schema: dict | None = None, disable_thinking: bool = False) -> str:
        if not network_enabled():
            raise ProviderUnavailable("LLM_NETWORK_ENABLED is not true: network calls are off")
        client = self._sdk()
        row = self.ledger.reserve(purpose, model, digest, estimate_input_tokens(system, user), max_tokens)
        output_config: dict = {"effort": "low"}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if disable_thinking:
            kwargs["thinking"] = {"type": "disabled"}
        counts = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        status = "error"
        try:
            try:
                response = client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - timeout, 4xx/5xx, connection, anything: the template is used
                raise ProviderUnavailable(f"anthropic: {type(exc).__name__}") from None
            counts = usage_counts(getattr(response, "usage", None))
            status = "ok"
            try:
                from app import observability

                observability.record_llm_usage(PROVIDER_NAME, counts)  # closure_llm_tokens_total{provider,kind} (F14)
            except Exception:  # noqa: BLE001 - a metric must never fail a draft
                pass
            self.last_usage = counts
            stop = getattr(response, "stop_reason", None)
            if stop == "refusal":
                status = "refused"
                raise ProviderResponseError("anthropic: the model declined the request")
            if stop == "max_tokens":
                status = "truncated"
                raise ProviderResponseError("anthropic: output cut off at the token limit")
            text = "".join(getattr(b, "text", "") for b in (getattr(response, "content", None) or [])
                           if getattr(b, "type", None) == "text").strip()
            if not text:
                status = "empty"
                raise ProviderResponseError("anthropic: empty answer")
            return text
        finally:
            self.ledger.settle(row, counts, status)


class ClaudeProvider(_ClaudeCaller):
    """Drafting provider (Haiku). Registered as `anthropic`, wrapped by the output cache."""

    name = PROVIDER_NAME
    fills_placeholders = True  # the prompt asks for {owner_name}, {next_step}, {appointment_date}; draft_with_fallback fills them

    @property
    def model_id(self) -> str:
        return draft_model()

    def draft(self, req: DraftRequest) -> DraftResult:
        digest = prompt_hash(req)
        text = self.call(purpose=f"draft:{req.purpose}", model=self.model_id, system=SYSTEM_PROMPT,
                         user=build_prompt(req), digest=digest, max_tokens=max_tokens_per_call(),
                         disable_thinking=True)
        return DraftResult(text=text, language=req.language, provider=self.name, model_id=self.model_id,
                           fallback_used=False, abstention_reason=None, prompt_hash=digest, usage=self.last_usage)


class ClaudePlanAdapter(_ClaudeCaller):
    """What `app.agency.planner.ModelPlanner` calls for a plan (Sonnet): `complete(system, user, max_tokens)` -> JSON text."""

    name = PROVIDER_NAME

    @property
    def model_id(self) -> str:
        return plan_model()

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        from app.agency.planner import prompt_hash as plan_hash

        # The planner asks for 800; the configured per-call cap is the ceiling, and lower is allowed.
        return self.call(purpose="plan", model=self.model_id, system=system, user=user,
                         digest=plan_hash(system, user), max_tokens=min(max_tokens, max_tokens_per_call()),
                         schema=PLAN_SCHEMA)
