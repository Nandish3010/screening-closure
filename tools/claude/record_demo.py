#!/usr/bin/env python3
"""Record the offline demo cache with Claude (A35, ADR-043). Manual: `make claude-record`. Needs the network and a key.

Records, in `LLM_CACHE_MODE=record`, into api/app/ai/cache/:

* drafting (Haiku): every purpose x channel x language for each demo persona (the care owners and staff the demo logs in as),
  through the real drafting pipeline (`draft_with_fallback`), so the file holds what the validator saw;
* planning (Sonnet): one agency run over the 22-case cohort of api/tests/agency_world.py (the seeded cohort's size and ids,
  walked to a spread of states), with `AGENCY_PLANNER=claude`. Each department head's plan is recorded under cache/agency/anthropic/.

A plan replays only when the head's candidate list is identical to the recorded one (the prompt hash covers case ids, states
and due times); in any other run the head uses the rule order and says `cache_miss`. Drafts replay for any case whose owner
and appointment date match a recorded persona; otherwise the template is used and `cache_miss` is counted.

Token use is printed and kept in data/llm_usage_record.sqlite (counts only). The key is read from the environment or .env and
never printed or written.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

REPO = _env.ROOT
for p in (REPO / "api", REPO / "api" / "tests"):
    sys.path.insert(0, str(p))

# (role, display name): the demo cast (tools/demo/scenario.py) and the sample owner the tests use.
PERSONAS = (
    ("primary_care_owner", "Dr. Layla Haddad"),
    ("primary_care_owner", "Dr. Huda Rahman"),
    ("specialist_owner", "Dr. Rana Farouk"),
    ("specialist_owner", "Dr. Karim Nasser"),
    ("coordinator", "Maryam Al Suwaidi"),
    ("supervisor", "Dr. Noura Al Hammadi"),
    ("primary_care_owner", "Dr Sara Khan"),
)


def record_drafts(personas=PERSONAS) -> Counter:
    from app.ai.provider import CHANNELS, LANGUAGES, PURPOSES, DraftRequest, get_provider
    from app.ai.templates import DEFAULT_NEXT_STEP, SAMPLE_VALUES
    from app.ai.validate import AbstentionLog, draft_with_fallback

    provider = get_provider("anthropic")
    log = AbstentionLog()
    out: Counter = Counter()
    for role, name in personas:
        for language in LANGUAGES:
            for purpose in PURPOSES:
                for channel in CHANNELS:
                    date = SAMPLE_VALUES[language]["appointment_date"] if purpose == "appointment_confirmation" else None
                    req = DraftRequest(purpose, channel, language, name, role, DEFAULT_NEXT_STEP[language],
                                       "colorectal_fit", date)
                    result = draft_with_fallback(req, provider, log)
                    out["drafts"] += 1
                    if result.fallback_used:
                        out["abstained"] += 1
                        out[f"abstained:{(result.abstention_reason or '').split(':')[0]}"] += 1
    return out


def record_agency() -> dict:
    import agency_world as world
    from app.agency import runner
    from app.clock import clock

    world.fresh_world()
    world.build_cohort()
    try:
        summary = runner.run_agency(trigger="manual")
        heads = runner.ledger().query(agent_type="department_head")
        return {"status": summary["status"], "heads": len(heads),
                "planned_by_claude": sum(1 for t in heads if not t.fallback_used),
                "fallbacks": Counter(t.output.get("abstention_reason") for t in heads if t.fallback_used)}
    finally:
        clock.reset()


def main() -> int:
    if not _env.load_key():
        print("claude-record: ANTHROPIC_API_KEY is not set in the environment or .env")
        return 1
    os.environ.update({"LLM_PROVIDER": "anthropic", "LLM_NETWORK_ENABLED": "true", "LLM_CACHE_MODE": "record",
                       "AGENCY_PLANNER": "claude"})
    os.environ.setdefault("LLM_DAILY_TOKEN_BUDGET", "600000")
    os.environ.setdefault("LLM_USAGE_DB", str(REPO / "data" / "llm_usage_record.sqlite"))
    from app.ai import cache, claude

    print(f"claude-record: drafting {claude.draft_model()}, planning {claude.plan_model()}, cache {cache.cache_dir()}")
    drafts = record_drafts()
    print(f"claude-record: drafts {dict(drafts)}")
    agency = record_agency()
    print(f"claude-record: agency run {agency}")
    rows = claude.UsageLedger().rows()
    by_model: Counter = Counter()
    for r in rows:
        by_model[(r["model"], "in")] += r["input_tokens"]
        by_model[(r["model"], "out")] += r["output_tokens"]
        by_model[(r["model"], "cache_read")] += r["cache_read_input_tokens"]
        by_model[(r["model"], "calls")] += 1
    for (model, kind), n in sorted(by_model.items()):
        print(f"claude-record: {model} {kind} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
