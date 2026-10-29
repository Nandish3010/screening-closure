# Model card: Claude planner

Long-form record: [agency.md](../agency.md) and [ai-boundary.md](../ai-boundary.md) sections 8 and 9. Template: [README.md](README.md).

## Summary

| Field | Value |
|---|---|
| Model / version id | `claude-sonnet-5-5` (default, override `ANTHROPIC_PLAN_MODEL`), `effort: low`; the rule planner is `agency-template-planner-v1`. Each plan stores `prompt_hash`, `provider`, `model_id`, `fallback_used` on its ledger row |
| Kind | Hosted large language model ordering a fixed list of candidate tasks, JSON output with a strict schema |
| Provider | Anthropic, only when `AGENCY_PLANNER=claude`; replay cache by default (same network and in-country caveats as [claude-drafting.md](claude-drafting.md)) |
| Licence | Anthropic commercial API terms (to verify) |
| Where in code | `api/app/agency/planner.py`, `api/app/ai/claude.py` (`PLAN_SCHEMA`), `api/app/agency/` (runner, agents, ledger), `api/app/routers/agency.py` |
| Owner role | Programme lead (to be named) |

## Intended use

A department head of the agency has already chosen its candidate tasks by rules. The planner returns an order for them and a rationale of at most 400 characters for the operations team. The head executes exactly its own candidates in that order; a task the planner leaves out is appended at the end. Work that needs a person lands in the `needs_human` inbox (`GET /agency/inbox`).

## Out-of-scope uses

Adding, dropping or changing a task, naming a tool, changing any case state (agency actors are listed in `AGENCY_NEVER` in `api/app/policy.py`, and the state machine refuses any event from an agency actor), approving a message, closing, consent, or reading patient text: the view an agent has of a case (`CaseFacts`) has no text field. The only patient-facing text an agent can cause is a draft through the drafting route with its validator and approval.

## Data

No training by us. The prompt holds the department, agent types, case ids as random UUIDs, reason codes and a fixed set of label keys (state, overdue, escalation level, due time, counts, day); synthetic in this prototype. Recorded plans live under `<cache dir>/agency/anthropic/<prompt hash>.json` (none tracked in this worktree).

## Evaluation

Boundary tests, not accuracy. Source: `tests/redteam/results.json` `summary.agency`: 92 cases, 92 pass, 0 exposed (category RT-A; [red-team.md](../red-team.md) section 5.4). The agency tests T-A61 to T-A66 (`api/tests/test_agency.py`) show that a full run changes no case state, that plans and dispatches are identical whatever the risk ordering aid says, that free text never reaches a planner or the ledger, and that a bad reply, a cache miss or no network falls back to the rule order. agency.md section 10 states plainly that the tests "do not measure benefit". No measure of plan quality against rule order exists.

## Limitations

- Planning quality versus the rule order is unmeasured; the rule order is the safe baseline.
- Wall-clock run budget (`AGENCY_RUN_BUDGET_SECONDS`, default 20) means a slow machine plans differently; the next run replans.
- The run lock is per process; several replicas rely on deterministic ids (agency.md section 11).
- Fairness and anomaly thresholds in the quality department (10 percentage points, at least 5 cases) are not clinically reviewed (H1).
- Approval fatigue on agent-drafted messages is unmeasured (HZ-29); runaway task loops are HZ-26; injection through notes is HZ-28.

## Subgroup performance

The planner never sees language or any patient attribute, so it has no per-language output to measure ([fairness.md](../../results/fairness.md) covers triage, the risk score and the validator). The agency's own `fairness_watch` task (Quality and Operations) looks at the drafting abstention split. No subgroup evaluation of the planner's ordering exists, and none is claimed.

## Human oversight

Everything in the inbox is a person's: approving drafts, accepting escalations, reroutes, referrals, closure, consent and reading every patient reply (agency.md section 4). `GET /agency/org`, `/agency/runs` and `/agency/tasks` expose every plan with its provenance to the coordinator and supervisor. A run is started only by a supervisor, the agency service token, or the scheduler hook.

## Monitoring

Exists in code today: per-plan `fallback_used` and reason on the ledger (`GET /agency/runs`), run status including `budget_exhausted`, `closure_llm_tokens_total{provider,kind}`, the shared token budget, and the failure-rate anomaly check in the Quality department. No Prometheus alert on planner fallbacks exists yet; proposed thresholds are in [ai-incidents.md](../ai-incidents.md) section 5.

## Kill switch

`AGENCY_PLANNER=template` (read at `api/app/agency/planner.py:272`): plans by rules alone, no model call. To stop agency runs entirely from the scheduler, `AGENCY_EVERY_SWEEPS=0` (`api/app/agency/runner.py:490`; compose default `docker-compose.yml:140`); `POST /agency/run` still works for a supervisor. `LLM_NETWORK_ENABLED=false` also stops outbound calls. See [ai-incidents.md](../ai-incidents.md) section 4.

## Owner and review

Owner: Programme lead (to be named), with the Clinical safety officer (to be named, H1) consulted. Review cadence: on any change of planner model, prompt or schema, and at least every six months. Last reviewed: 2026-10-25. Reviewed by: AI-assisted engineering review only; no clinician or native-Arabic review yet.

## Changelog

- 2026-10-25: card created in the uniform template.
- A22 / ADR-033: agency and rule planner; A35 / ADR-043: Claude planner added.
