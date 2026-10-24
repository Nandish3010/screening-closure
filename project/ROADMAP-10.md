# Roadmap to 10/10

Goal: 9 to 10 on every judging criterion by 18 Nov 2026 (submission window 16 to 18 Nov). Written 2026-10-22. Tickets live in [TRACKER.md](TRACKER.md) under milestone M6 (target 15 Nov); test cases are planned in [TEST-CASES.md](TEST-CASES.md). Criteria are the official six in [PLAN.md](PLAN.md) section 1.

## Honest ceilings

- Health impact tops out near 9 without a licensed clinician reviewing the pathway (H1 stays a human task; the AI-simulated review in docs/governance/clinician-review.md does not count as one).
- Commercial tops out near 8 without a named buyer or letter of intent. The business case (E17) can be rigorous, but it cannot invent a customer.
- A 10 on any criterion is a target, not a promise. E20 (independent re-score by three reviewers) is how we find out.

## Criteria

| Criterion | Consensus now | Target | Work items |
|---|---|---|---|
| Technical | 8 to 9 | 10 | F13 CI and fresh clone, A27 exhaustive model check, A30 wall-clock mode, B20 held-out validation, F14 observability, E19 docs refresh |
| Responsible AI | 7 to 8 | 9 to 10 | D14 red-team suite, A32 erasure (ADR-025), A29 OIDC login, E18 PDPL and ADHICS, A27 exhaustive check, D15 live triage behind the validator |
| Demo | 7 | 9 | C12 UI design pass, C13 judge mode, D15 triage live on host, A30 wall-clock mode, week 4 rehearsals and final video |
| Scalability | 5 to 6 | 8 to 9 | B19 finite capacity, A28 HL7 v2 adapter, A31 leader election, F14 observability and deployment templates, F13 CI |
| Health impact | 7 | 9 (ceiling) | A33 informed-refusal path, B19 finite capacity, B20 held-out validation, H1 human clinician review |
| Commercial | 4 to 5 | 8 (ceiling) | E17 business case, E18 compliance tables, buyer conversations (human) |

## Weekly sequence

One line per ticket: what, why it moves the score, definition of done.

### Week 1 (22 to 26 Oct)

- **F13**: CI and fresh clone on GitHub Actions: `make check` and the fresh-clone script run on a clean runner on every push. Moves: Technical, Scalability. Done when: Workflow run green on a clean runner; badge in README; run URL recorded in LOG.md.
- **A27**: Exhaustive model check of the closure state machine: enumerate every reachable (state, event) pair up to a depth bound and prove the closure invariant holds on all of them. Moves: Technical, Responsible AI. Done when: Reports the number of states and transitions checked; no counterexample; runtime under 60 s.
- **B19**: Finite colonoscopy capacity in the simulator: slots per week per site, queueing delay and spill-over, with a capacity sweep. Moves: Scalability, Health impact. Done when: Capacity sweep in results.json and results.md; unlimited-capacity run byte-identical to the previous results.
- **A28**: HL7 v2 adapter (ORU for results, SIU for scheduling, ADT for admissions), Malaffi-shaped messages mapped to the existing FHIR-style events. Moves: Scalability, Technical. Done when: Three message types round-trip into the event log; sample messages and a field-mapping table in docs/.
- **E17**: Commercial business case: buyer, price per screened member or per closed case, unit economics from the effort model, rollout cost, and what is assumed. Moves: Commercial. Done when: Document with unit economics, a named target buyer segment and a sensitivity range; assumptions labelled.

### Week 2 (27 Oct to 2 Nov)

- **A29**: OIDC login through local Keycloak as a stand-in for UAE PASS, with token expiry and refresh, replacing the demo role selector. Moves: Responsible AI, Scalability, Technical. Done when: Keycloak realm in compose; login flow in the web app; token expiry test; RBAC matrix green. **Delivered (M6 week 2):** `AUTH_MODE=oidc` verifies provider JWTs against a local Keycloak in compose (`make oidc-up`), the web app signs in with the code flow and PKCE, dev tokens expire after 8 hours and oidc tokens at `exp` (ADR-040, docs/governance/identity.md). UAE PASS itself is not integrated; its claims and assurance level are to verify.
- **A30**: Wall-clock mode: scheduler and SLA clocks run on real time as well as the simulated clock. Moves: Technical, Demo. Done when: Setting documented; both clock modes covered by tests; replay unaffected. **Delivered (M6 week 2):** `CLOCK_MODE=sim|wall`, wall required outside dev, switching modes leaves the log intact (ADR-041, docs/governance/operations.md).
- **A31**: Scheduler leader election so several API replicas run one sweep, not many. Moves: Scalability, Technical. Done when: Three replicas in compose; handover time recorded; no duplicate or lost events. **Delivered (M6 week 2), with two replicas, not three:** a Postgres lease with a fencing token (migration 015), `make up-scale` runs two API replicas and two schedulers, and a killed leader was replaced in 10.1 s with a 10 s TTL, no event lost or duplicated (ADR-041). The third replica of the done-when line was not run.
- **D14**: Red-team suite for the AI edge: validator, agency boundary, triage, and prompt injection through patient replies. Moves: Responsible AI. Done when: Corpus of at least 100 attacks; zero bypasses; report in docs/governance/.
- **A32**: Erasure per ADR-025: erase a patient's data while the append-only log keeps verifiable structure. Moves: Responsible AI. Done when: Implements ADR-025; audit record of the erasure; replay test green. **Delivered (M6 week 2):** two-supervisor request and approve through `/erasures`, HAPI delete and expunge, tombstones, audit trail kept, migration 016, hazard HZ-34, model checker invariant I8 (docs/governance/erasure.md). Still open: the conflict with the medical-record retention duty and backup handling, both to verify.
- **A33**: Informed-refusal closure path: a patient declines screening and the case closes as declined with a human sign-off. Moves: Health impact, Responsible AI. Done when: New state and transition in the pathway config; clinician-review proposal implemented; invariant property test extended. **Delivered (M6 week 2), with A34 (cancel and rebook an appointment, HL7 SIU S15):** `declined_pending_signoff`, `record_informed_refusal` and a declined `close` signed by a physician who is not the counsellor, pathway v0.3 (ADR-042). The model checker now covers eight invariants over 17,954 abstract states. The simulator's refusal path is switched off in the committed results on purpose, and the re-offer interval and the signature rules are open for the clinical lead.
- **B20**: Simulator validation on held-out data: fit on one split, compare simulated and observed on the held-out split. Moves: Technical, Health impact. Done when: Validation section in results.md with the tolerance stated and met, or the gap stated. **Delivered (M6 week 2), gap stated:** docs/results/validation.md compares the simulator with data it was not calibrated on. The completion ratio is inside three trial intervals and the baseline level for typical US usual care is within tolerance; early timing is badly wrong. **Open follow-up B21:** fix the simulator's timing structure and re-run validation (in TRACKER.md, status todo). Claims about days saved or a 30-day SLA should wait for it.

### Week 3 (3 to 9 Nov)

- **C12**: UI design pass: type, spacing, empty and error states, Arabic and RTL polish, accessibility checks. Moves: Demo. Done when: Before and after screenshots; no serious accessibility violation; RTL screens reviewed.
- **F14**: Observability (OpenTelemetry traces, metrics and a dashboard) and deployment templates (Bicep or Terraform, plus a Helm chart). Moves: Scalability, Technical. Done when: Dashboard shows sweep duration, queue depth and error rate; templates validate in CI.
- **E18**: PDPL impact assessment and an ADHICS control table mapped to what the system does. Moves: Responsible AI, Commercial. Done when: Impact assessment and control table committed; gaps listed honestly.
- **C13**: Judge mode: a guided read-only walkthrough that shows the closure invariant, the evidence and the numbers in a few clicks. Moves: Demo. Done when: A three-minute self-serve path; no write route reachable; e2e test green.
- **D15**: Triage live on the host: the triage assistant runs against a real model on the host, not only cached outputs, behind the same validator. Moves: Demo, Responsible AI. Done when: Live run recorded with latency and fallback rate; offline test still green.
- **E19**: Documentation refresh: README, technical documentation and PLAN reflect M6, with stale numbers corrected. Moves: Technical, Demo. Done when: No stale number left; links and tables clean.
- **E20**: Independent re-score by three reviewers against the six judging criteria, with the scores and the gaps recorded. Moves: All six. Done when: Three score sheets, the average per criterion and a list of gaps fixed or accepted.

### Week 4 (10 to 15 Nov)

- Feature freeze on 10 Nov except fixes found in rehearsal.
- Human rehearsals (H3), final video (H4), fresh-clone run on a second machine (H5), Q&A drill. These are human-owner tasks in TRACKER.md.

### 16 to 18 Nov

- Submit (H6). Re-check the form against the requirements shared at registration first.

## Dependencies and risks

- Week 1 is the base: F13 and A27 gate the Technical claim, and A28 and B19 carry most of the Scalability gain.
- A29 (Keycloak) touches every route, so run the RBAC matrix before and after.
- D14 waits on D15, which needs a model key on the host.
- H1 (clinician) and a named buyer are outside the code. Book them this week; they decide whether the ceilings are reachable.
