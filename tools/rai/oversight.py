#!/usr/bin/env python3
"""Human-oversight evidence from a seeded demo run (ticket M7-5). `make rai-oversight` / `make rai-oversight-check`.

Two parts, kept apart on purpose:

* `analyse(events, ...)` reads an event log and measures oversight. It knows nothing about how the log was made, so the
  same function can be pointed at a pilot's exported log later.
* `seeded_run()` makes a log to analyse: the REAL API, in process (FastAPI TestClient, in-memory stores, no database, no
  network), driven through a seeded set of cases. Drafting runs as the demo does (`LLM_PROVIDER=anthropic`, network off,
  replay cache pointed at an empty directory, so every draft is a cache miss and the validator-checked template is
  used); reply triage replays the recorded model scores of the triage test split through the deployed decision path
  (tools/rai/fairness.py explains the replay); one agency run plans over the cohort (`AGENCY_PLANNER=claude`, which
  falls back to the rules planner with the network off).

What is MEASURED and what is SCRIPTED: the people in the run are a seeded script. Their approve / reject / redraft
choices and how long they take (`REVIEWER` below) are inputs, so the shares and the review times only show that the
log captures them; they are not findings about real staff. The fallback and abstention rates and reasons, the triage
routing, and the guarantee (no AI event moved a case's state; every message sent from a draft was approved by a
person first) are measured on what the real code did.

Writes docs/results/oversight.md and oversight.json. Deterministic: `--check` regenerates and compares byte for byte.

Usage: PYTHONPATH=api:. python tools/rai/oversight.py [--check]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import logging
logging.disable(logging.INFO)
OUT_JSON = ROOT / "docs/results/oversight.json"
OUT_MD = ROOT / "docs/results/oversight.md"
SEED = 20261025
N_CASES = 60
NS = UUID("6f1d3c0e-5b7a-4e21-9a3c-0d5e7b9f1a24")

# The scripted reviewer (inputs, not findings).
REVIEWER = {
    "approve": 0.75,  # approve the first draft
    "redraft": 0.15,  # reject with a reason (tone / inaccurate / wrong_language), ask for a new draft, approve that
    "reject": 0.10,  # reject as not needed; nothing is sent
    "review_delay_hours": {"median": 3.0, "sigma": 0.8},  # log-normal working-hours delay before the decision
}
LANGUAGE_MIX = {"ar": 0.55, "en": 0.45}
CHANNELS = ("sms", "whatsapp")
OWNER = "Dr Sara Khan"
AI_ACTOR_PREFIXES = ("model:", "system:triage", "system:agency")


# ================================================================================================ analysis


def is_ai(actor: str) -> bool:
    return actor.startswith(AI_ACTOR_PREFIXES)


def analyse(case_events: dict[str, list], pathway, agency: dict | None = None) -> dict:
    """Oversight measures from an event log: {case_id: [Event, ...]} in log order."""
    from app import state_machine as sm

    drafted, decided, sent_from_draft, unapproved_sends = {}, {}, 0, []
    ai_events, ai_state_changes = Counter(), []
    fallback_reasons, provider_counts = Counter(), Counter()
    triage = Counter()
    triage_reasons = Counter()
    for cid, events in case_events.items():
        model = sm.initial_model(pathway)
        approved: set[str] = set()
        for n, ev in enumerate(events):
            before = model.state if n else None
            model = sm.genesis(model, ev, pathway) if n == 0 else sm.step(model, ev, pathway)
            p = ev.payload or {}
            if is_ai(ev.actor):
                ai_events[ev.type] += 1
                if n and model.state != before:
                    ai_state_changes.append({"case": cid, "seq": n, "type": ev.type, "actor": ev.actor, "from": before, "to": model.state})
            if ev.type == "message_drafted":
                drafted[p["draft_id"]] = {"at": ev.at, "fallback": p.get("fallback_used"), "provider": p.get("provider"), "language": p.get("language")}
                provider_counts[p.get("provider")] += 1
            elif ev.type in ("message_approved", "message_rejected") and p.get("draft_id") in drafted:
                decided[p["draft_id"]] = {"decision": ev.type, "at": ev.at, "actor": ev.actor, "reason": p.get("reason")}
                if ev.type == "message_approved" and not sm.is_automated(ev.actor):
                    approved.add(p["draft_id"])
            elif ev.type == "message_sent" and p.get("draft_id"):
                sent_from_draft += 1
                if p["draft_id"] not in approved:
                    unapproved_sends.append({"case": cid, "draft_id": p["draft_id"]})
            elif ev.type == "reply_classified":
                triage["replies"] += 1
                triage["needs_human" if p.get("needs_human") else "auto_handled"] += 1
                if p.get("model_id") == "unavailable":
                    triage["model_unavailable"] += 1
                if str(p.get("model_id", "")).startswith("rule:"):
                    triage["opt_out_gate"] += 1
            elif ev.type in ("review_requested", "callback_requested") and ev.actor == sm.TRIAGE_ACTOR:
                triage_reasons[f"{ev.type}:{p.get('reason')}"] += 1

    n_drafts = len(drafted)
    decisions = Counter(d["decision"] for d in decided.values())
    delays = sorted((d["at"] - drafted[k]["at"]).total_seconds() / 3600 for k, d in decided.items())
    human_decided = sum(1 for d in decided.values() if not sm.is_automated(d["actor"]))
    return {
        "cases": len(case_events),
        "events": sum(len(v) for v in case_events.values()),
        "drafts": {
            "n": n_drafts,
            "by_provider": dict(sorted(provider_counts.items())),
            "fallback_used": sum(1 for d in drafted.values() if d["fallback"]),
            "fallback_rate": round(sum(1 for d in drafted.values() if d["fallback"]) / n_drafts, 4) if n_drafts else None,
            "fallback_reasons": dict(sorted(fallback_reasons.items())),
            "decided_by_a_person": human_decided,
            "undecided": n_drafts - len(decided),
            "approved": decisions["message_approved"],
            "rejected": decisions["message_rejected"],
            "reject_reasons": dict(sorted(Counter(d["reason"] for d in decided.values() if d["decision"] == "message_rejected").items())),
            "median_hours_to_human_decision": round(statistics.median(delays), 2) if delays else None,
            "p90_hours_to_human_decision": round(delays[int(0.9 * (len(delays) - 1))], 2) if delays else None,
        },
        "triage": {**dict(sorted(triage.items())), "effects": dict(sorted(triage_reasons.items()))},
        "agency": agency,
        "guarantee": {
            "ai_events_by_type": dict(sorted(ai_events.items())),
            "ai_events": sum(ai_events.values()),
            "ai_events_that_changed_state": ai_state_changes,
            "messages_sent_from_drafts": sent_from_draft,
            "sent_without_prior_human_approval": unapproved_sends,
            "holds": not ai_state_changes and not unapproved_sends,
        },
    }


# ================================================================================================ the run


def seeded_run() -> tuple[dict, dict]:
    cache_dir = tempfile.mkdtemp(prefix="rai-oversight-cache-")
    os.environ.update({"LLM_PROVIDER": "anthropic", "LLM_NETWORK_ENABLED": "false", "LLM_CACHE_MODE": "replay",
                       "LLM_CACHE_DIR": cache_dir, "AGENCY_PLANNER": "claude", "TRIAGE_ENABLED": "1"})
    os.environ.pop("TRIAGE_REMOTE_URL", None)

    from fastapi.testclient import TestClient

    from app import clock as clock_mod  # noqa: F401  (the sim clock the routes read)
    from app.ai.draft_store import InMemoryDraftStore
    from app.events import InMemoryEventStore
    from app.main import app
    from app.routers import cases, drafts, replies
    from app.triage import classify as triage
    from app.triage.reply_store import InMemoryReplyStore
    from fairness import ReplayScorer, load_split, DATA

    cases.store = InMemoryEventStore()
    drafts.store = InMemoryDraftStore()
    replies.store = InMemoryReplyStore()
    test = load_split("test")
    scores = json.loads((DATA / "triage_scores_test.json").read_text())
    triage.set_backend(ReplayScorer(test, scores), triage.Calibration.load())

    rng = random.Random(SEED)
    client = TestClient(app)
    tokens: dict[tuple, dict] = {}

    def H(role: str, name: str | None = None) -> dict:
        key = (role, name)
        if key not in tokens:
            r = client.post("/auth/dev-login", json={"role": role, "name": name or f"demo {role}"})
            assert r.status_code == 200, r.text
            tokens[key] = {"Authorization": f"Bearer {r.json()['token']}"}
        return tokens[key]

    def call(method: str, path: str, role: str, body=None, name=None, ok=(200, 201)) -> dict:
        r = client.request(method, path, json=body, headers=H(role, name))
        assert r.status_code in ok, f"{method} {path}: {r.status_code} {r.text}"
        return r.json()

    def event(cid: str, type_: str, role: str, payload=None, name=None) -> None:
        call("POST", f"/cases/{cid}/events", role, {"type": type_, "payload": payload or {}}, name)

    def advance(hours: float) -> None:
        whole = max(1, round(hours))
        call("POST", "/clock/advance", "supervisor", {"hours": whole})

    call("POST", "/clock/reset", "supervisor")
    staff_drafts = 0
    reasons = ("tone", "inaccurate", "wrong_language")
    picks = rng.sample(range(len(test)), N_CASES)
    case_ids = []
    for i in range(N_CASES):
        patient = f"Patient/rai-{i:03d}"
        cid = str(uuid5(NS, patient))
        call("POST", "/cases", "coordinator", {"patient_ref": patient, "case_id": cid})
        case_ids.append(cid)
        event(cid, "acknowledge", "lab")
        event(cid, "accept_ownership", "primary_care_owner", name=OWNER)
        event(cid, "contact_patient", "primary_care_owner", name=OWNER)
        lang = "ar" if rng.random() < LANGUAGE_MIX["ar"] else "en"
        body = {"purpose": "reminder", "channel": rng.choice(CHANNELS), "language": lang}
        roll = rng.random()
        draft = call("POST", f"/cases/{cid}/drafts", "primary_care_owner", body, OWNER)
        staff_drafts += 1
        advance(rng.lognormvariate(0, REVIEWER["review_delay_hours"]["sigma"]) * REVIEWER["review_delay_hours"]["median"])
        if roll < REVIEWER["approve"]:
            call("POST", f"/drafts/{draft['draft_id']}/approve", "primary_care_owner", None, OWNER)
        elif roll < REVIEWER["approve"] + REVIEWER["redraft"]:
            call("POST", f"/drafts/{draft['draft_id']}/reject", "primary_care_owner", {"reason": rng.choice(reasons)}, OWNER)
            draft = call("POST", f"/cases/{cid}/drafts", "primary_care_owner", body, OWNER)
            staff_drafts += 1
            advance(rng.lognormvariate(0, REVIEWER["review_delay_hours"]["sigma"]) * REVIEWER["review_delay_hours"]["median"])
            call("POST", f"/drafts/{draft['draft_id']}/approve", "primary_care_owner", None, OWNER)
        else:
            call("POST", f"/drafts/{draft['draft_id']}/reject", "primary_care_owner", {"reason": "not_needed"}, OWNER)
        row = test[picks[i]]
        call("POST", f"/cases/{cid}/replies", "coordinator", {"text": row["text"], "channel": "whatsapp",
                                                              "reply_id": str(uuid5(NS, f"reply-{i}"))})

    from app.agency import runner as agency_runner

    agency_runner.reset_memory_ledger()
    run = call("POST", "/agency/run", "supervisor")
    tasks = call("GET", f"/agency/tasks?run_id={run['run_id']}&limit=5000", "supervisor")
    planners = Counter((t["provenance"]["provider"], bool(t["provenance"]["fallback_used"])) for t in tasks if t.get("agent_type") == "department_head")
    agency = {
        "status": run.get("status"),
        "planner_configured": (run.get("planner") or {}).get("name"),
        "tasks": len(tasks),
        "by_status": dict(sorted(Counter(t["status"] for t in tasks).items())),
        "department_head_plans": {f"{prov} fallback={fb}": n for (prov, fb), n in sorted(planners.items(), key=str)},
        "abstention_reasons": dict(sorted(Counter((t.get("output") or {}).get("abstention_reason") for t in tasks
                                                   if (t.get("output") or {}).get("abstention_reason")).items())),
    }
    triage.reset()

    abst = call("GET", "/metrics/abstention", "supervisor")
    events = {cid: cases.store.events_for(UUID(cid)) for cid in case_ids}
    result = analyse(events, cases.pathway, agency)
    result["drafts"]["fallback_reasons"] = abst.get("by_reason") or {}
    result["drafts"]["requested_by_staff"] = staff_drafts
    result["drafts"]["drafted_by_agency_awaiting_a_person"] = result["drafts"]["n"] - staff_drafts
    result["abstention_metric"] = {k: abst.get(k) for k in ("attempts", "abstentions", "rate", "by_language", "by_channel")}
    return result, {"seed": SEED, "cases": N_CASES, "reviewer": REVIEWER, "language_mix": LANGUAGE_MIX}


# ================================================================================================ report


def pct(n: int, d: int) -> str:
    return f"{n} of {d} ({100 * n / d:.1f}%)" if d else "0 of 0"


def render_md(res: dict, setup: dict) -> str:
    d, t, g, a = res["drafts"], res["triage"], res["guarantee"], res["agency"]
    L = ["# Human oversight evidence (M7-5)", "",
         "Generated by `tools/rai/oversight.py` (`make rai-oversight`; `make rai-oversight-check` fails if this file differs from a fresh run). "
         "Do not edit by hand.", "",
         f"**Source.** A seeded demo run of the real API in process (seed {setup['seed']}, {setup['cases']} synthetic cases, in-memory stores, "
         "no database, no network). Drafting runs as the demo does: `LLM_PROVIDER=anthropic` with the network off and an empty replay cache. "
         "Reply triage replays the triage model's recorded scores for real test-split replies through the deployed decision path. "
         "One agency run plans over the cohort.", "",
         "**Measured versus scripted.** The people are a seeded script: approve the first draft "
         f"{100 * setup['reviewer']['approve']:.0f}% of the time, reject with a reason and approve a redraft {100 * setup['reviewer']['redraft']:.0f}%, "
         f"reject as not needed {100 * setup['reviewer']['reject']:.0f}%, after a log-normal delay (median {setup['reviewer']['review_delay_hours']['median']} h). "
         "So the decision shares and review times below show that the log records them; they are NOT findings about real staff. "
         "The fallback, abstention, triage routing and guarantee rows are what the real code did. "
         "A pilot runs `analyse()` on its exported event log to get the real shares.", "",
         "## AI outputs and human decisions", "",
         "| Measure | Value | Kind |", "|---|---|---|",
         f"| Drafts produced | {d['n']} (by provider: {', '.join(f'{k} {v}' for k, v in d['by_provider'].items())}) | measured |",
         f"| Drafts requested by staff in the script | {d['requested_by_staff']}, of which decided by a person: {pct(d['decided_by_a_person'], d['requested_by_staff'])} | measured |",
         f"| Drafts the agency prepared | {d['drafted_by_agency_awaiting_a_person']}, all waiting for a person (undecided: {d['undecided']}; none sent) | measured |",
         f"| Approved | {pct(d['approved'], d['requested_by_staff'])} | scripted |",
         f"| Rejected (an edit is a rejection with a reason and a new draft; the system has no in-place edit) | {pct(d['rejected'], d['requested_by_staff'])}; reasons: "
         + ", ".join(f"{k} {v}" for k, v in d["reject_reasons"].items()) + " | scripted |",
         f"| Median time from draft to human decision | {d['median_hours_to_human_decision']} h (p90 {d['p90_hours_to_human_decision']} h, simulated clock) | scripted |",
         f"| Template fallback | {pct(d['fallback_used'], d['n'])} | measured |",
         f"| Fallback and abstention reasons (`GET /metrics/abstention`) | " + (", ".join(f"`{k}` {v}" for k, v in d["fallback_reasons"].items()) or "none") + " | measured |",
         f"| Abstention metric | {res['abstention_metric']['abstentions']} of {res['abstention_metric']['attempts']} attempts | measured |",
         f"| Replies triaged | {t.get('replies', 0)} | measured |",
         f"| Sent to a person (`needs_human`) | {pct(t.get('needs_human', 0), t.get('replies', 0))} | measured |",
         f"| Auto-handled | {pct(t.get('auto_handled', 0), t.get('replies', 0))} | measured |",
         "| Triage effects written (all replies; none changes state) | " + ", ".join(f"`{k}` {v}" for k, v in t["effects"].items()) + " | measured |",
         f"| Caught by the opt-out gate before the model | {t.get('opt_out_gate', 0)} | measured |",
         f"| Agency run | {a['status']}, planner configured `{a['planner_configured']}`; {a['tasks']} tasks by status "
         + ", ".join(f"{k} {v}" for k, v in a["by_status"].items()) + "; department-head plans: "
         + ", ".join(f"{k} {v}" for k, v in a["department_head_plans"].items())
         + ("; planner abstentions: " + ", ".join(f"`{k}` {v}" for k, v in a["abstention_reasons"].items()) if a["abstention_reasons"] else "")
         + " | measured |",
         "",
         "## Why the fallback rate is what it is", "",
         "Every draft fell back to the template because the replay cache is empty in this run and the network is off: the Claude "
         "provider has nothing to replay, abstains with the reason above, and the validator-checked template is used. That is the shipped "
         "demo configuration (`docker-compose.yml`: `LLM_NETWORK_ENABLED=false`). With a recorded cache or the network on, the rate falls "
         "and the other reasons (`validation_failed:*`, provider errors, budget) appear; `GET /metrics/abstention` reports them by reason, "
         "language and channel in the same shape." if d["fallback_used"] == d["n"] else
         "The reasons above are the abstention reasons the drafting path recorded.", "",
         "## The guarantee: no AI output changed a case without a person", "",
         f"- {g['ai_events']} events in the log came from an AI actor (`model:*`, `system:triage`, `system:agency:*`): "
         + ", ".join(f"`{k}` {v}" for k, v in g["ai_events_by_type"].items()) + ".",
         f"- AI events after which the case state differed from before: **{len(g['ai_events_that_changed_state'])}**.",
         f"- Messages sent from a draft: {g['messages_sent_from_drafts']}; sent without a prior `message_approved` by a person: "
         f"**{len(g['sent_without_prior_human_approval'])}**.",
         f"- Holds on this run: **{'yes' if g['holds'] else 'NO'}**.", "",
         "This run is one sample. The proof for every reachable state is the model check (`make verify`, "
         "[verification.md](../governance/verification.md)): invariant I2 (no automated actor, `model:*` or the agency gets `close`, "
         "`reopen`, `clinician_review`, `message_approved`, `escalation_accepted`, `consent_updated` or `accept_ownership` accepted) and "
         "I3 (`message_sent` only for a draft a person approved, or a listed pre-approved template under a signed policy), with mutation "
         "tests M1 and M3 showing the checker catches a break. Unit evidence: `test_a_reply_never_closes_or_advances_a_case_in_any_state` "
         "(api/tests/test_replies.py) and T-A61 `test_t_a61_a_full_run_changes_no_case_state_and_writes_only_model_drafts` (api/tests/test_agency.py).", ""]
    return "\n".join(L)


def build() -> tuple[str, str]:
    res, setup = seeded_run()
    payload = {"ticket": "M7-5", "setup": setup, **res}
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n", render_md(res, setup)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    js, md = build()
    if not json.loads(js)["guarantee"]["holds"]:
        print("rai-oversight: FAIL (an AI event changed state, or a draft was sent without human approval)")
        return 1
    if args.check:
        stale = [str(p.relative_to(ROOT)) for p, text in ((OUT_JSON, js), (OUT_MD, md)) if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print(f"rai-oversight: FAIL ({', '.join(stale)} differ from a fresh run; run `make rai-oversight`)")
            return 1
        print("rai-oversight: PASS (guarantee holds, files up to date)")
        return 0
    OUT_JSON.write_text(js, encoding="utf-8")
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"rai-oversight: wrote {OUT_JSON.relative_to(ROOT)} and {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
