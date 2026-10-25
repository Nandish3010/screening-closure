# Model checking the closure engine (A27)

The state machine (`api/app/state_machine.py`, `step`) decides whether every event in the system is valid. Property tests sample random event sequences (T-A1). This document describes an exhaustive check: an explicit-state model checker, `tools/verify/model_check.py`, explores every abstract state the machine can reach from a new case and checks eight invariants on every reachable state and transition. The decision is recorded in [ADR-038](../../project/DECISIONS.md).

Run it with `make verify`. It is pure Python, needs no database and no network, and is a stage of `make check` right after `test`. It exits non-zero on a violation and prints a shortest counterexample.

## What is checked

The checker calls the real `step()`. The machine is not reimplemented. From the genesis model of the shipped pathway (`config/pathways/colorectal_fit.yaml`), the search applies every event of a finite alphabet to one concrete representative of every abstract state, breadth-first. It stops when a level adds no new abstract state. The alphabet is every event type, crossed with every actor, crossed with representative payloads. Each invariant is checked as follows.

| | Invariant | How it is checked |
|---|---|---|
| I1 | `closed` is entered only by `close`. The closer must be a person in a physician role (`primary_care_owner` or `specialist_owner`; never `system:*`, `model:*` or the agency, and since E20a F1 never a patient, lab, coordinator or supervisor either), every accepted `clinician_review` must be by a physician role too (signed or not), the case must come from `clinician_reviewed`, and the event must carry non-empty `evidence_ref` and `signed_by`. A closed case accepts nothing except `reopen`. The signer must equal the actor and the evidence reference must be well formed. **Extended (A33, ADR-042):** a closure with `outcome: declined` comes from `declined_pending_signoff` (and only from there; a recorded refusal closes only as declined), cites exactly the counselling reference recorded with the refusal, and is made and signed by a physician role who is not the counsellor (compared by user id, so the actor and Practitioner forms are one person). The alphabet has the new events (`record_informed_refusal` with three counsellor forms and its malformed variants, `appointment_cancelled`, seven declined-close variants) and the `system:hl7` actor, and `record_informed_refusal` is in `HUMAN_DECISIONS` (I2). The refusal on record (counselling reference and counsellor) is part of the abstract state, because the declined close reads it. | On every transition into `closed`, and on every event accepted on a closed case. **Since A36 (ADR-044)** signer = actor is the machine's own rule, for every close (both outcomes) and every `clinician_review` that carries `signed_by`, and I1 checks it directly with the checker's own reading of a signer (same user id, as `role:user_id` or `Practitioner/<user_id>`); there is no route exception for it any more, and the alphabet has a review signed by a stranger. The shape of the evidence reference is still the route's (ADR-021): the checker runs the **real** route gate (`routers.cases._check_signature_and_evidence`) on every close the machine accepted with a malformed reference, and fails if the gate would let it through. |
| I2 | No automated actor (`system:*`, `model:*`, the agency, bare `scheduler`/`watchdog`) gets `close`, `reopen`, `clinician_review`, `message_approved`, `escalation_accepted`, `consent_updated` or `accept_ownership` accepted. The agency and actors that match no row of the actor table get nothing accepted. | On every accepted transition. The checker uses its own definition of "automated", not `sm.is_automated`, so a mutation of that function cannot hide itself. |
| I3 | `message_sent` is accepted only in two cases. Either it is from `system:notifier` for a draft in the approved-unsent set, or it is from `system:automation` with `auto: true`, a `<purpose>.<channel>` template and no draft. In both cases the patient must not have withdrawn consent, must consent to the channel, and must have no open `opt_out` or `wrong_number` hold. | On every accepted `message_sent`, against the model before it. The template allow-list is enforced at append time, not in the machine (ADR-032). The checker runs the **real** guard (`outreach_policy.auto_message_guard`, with the policy treated as signed) on every unlisted template the machine accepted, and fails if the guard would admit it. |
| I4 | While consent is frozen, no outreach is accepted: `contact_patient`, `message_drafted`, `message_approved`, `message_sent` or `handoff_requested`. `consent_frozen` is true exactly in `consent_withdrawn`. **Extended (A36, ADR-044):** while the referral is held (consent withdrawn, or `share_with_specialist` false) neither `handoff_requested` nor `handoff_accepted` is accepted and the scheduler does not bounce a pending request; and a `consent_updated` never cancels or alters a pending request (it may only add `resumed_at`). | On every accepted transition. The checker keeps its own list of outreach and referral events and its own reading of the hold, and does not read `CONSENT_FROZEN_EVENTS` or `referral_hold`. |
| I5 | Every reachable state other than `closed` has an event that some person may try through the route's gates and that the machine accepts. The gates are role policy (`EVENT_POLICY`), owner scope, payload schema, and the signer and evidence-shape checks. | On the saturated graph. |
| I6 | Replay is deterministic. Replaying the shortest trace to each representative as a log (`replay_model`) gives exactly the model the search holds, every field and not only the signature. A second replay gives the same result. | For every reachable abstract state. |
| I8 | Erasure (A32, ADR-025). `erasure_completed` is accepted only on a closed case, only from `system:erasure`, moves no state and marks the case erased; an erased case accepts nothing, not even `reopen`. The alphabet has the event and the actor; `erased` is part of the core signature. | On every accepted transition. |
| I7 | Bounded liveness. From every reachable state, some sequence of events that people may try (the I5 gates) reaches `closed`, or the state is a documented terminal hold. The list of documented holds (`DOCUMENTED_TERMINAL_HOLDS`) is empty: no hold is needed. | Backward reachability from `closed` over the human edges of the saturated graph. |

Every human edge used for I5 and I7 has passed the route's gates. Route-only approvals (`message_approved` and `message_rejected` through the drafts route) are not counted. Liveness is therefore proved with fewer human moves than people really have, which is the safe direction.

### The alphabet

The alphabet has 105 representative payloads, valid and invalid per schema:

- `close`: evidence present, absent, empty, and malformed (`Procedure/.`); signer self, `Practitioner/<self>`, someone else, absent and empty; a forged `auto` flag.
- `consent_updated`: withdraw, restore, a channel off, a channel on, sharing with the specialist off and on (A36; tried in the ownership slice), empty, and a non-boolean value.
- `clinician_review`: unsigned, signed by the reviewer's Practitioner, and signed by someone else (A36).
- `message_sent`: approved draft, unapproved draft, no draft, bad channel, listed template, unlisted template, template with a draft, and auto without a template.
- `review_requested`: every reason.
- Escalations: every rung on the state's ladder and on each milestone's ladder; the coalesced forms (`also_for`, `attached_to`); a bogus rung.
- `mark_overdue`: due and not yet due, the wrong state, a bad date, each milestone and an unknown milestone.
- Handoffs: request, accept, reject, the scheduler's bounce and a bogus reason.
- Reassignment, lost contact (2 and 3 attempts), triage relays, `watchdog_flagged`, `closure_proposed`, replies, and an unknown event type.

The actors are:

- one person per role, built as the API builds them (`auth.Identity`), so the route gates can run on them;
- every service row of the actor table: scheduler, watchdog, notifier, consent, automation, `model:`, triage and `system:agency:`;
- actors that match no row.

Every event happens at the same instant. Due dates are set relative to it, so no event is backdated and `mark_overdue` is due or not as the payload says.

## The abstraction

The read model holds timestamps, event ids, counters and histories, so it is infinite. The search keys states on a finite **signature**: everything `step` reads when it decides whether to accept an event and what the next signature is. The signature has four groups:

- **core:** the state, the consent freeze and withdrawn flag, `resume_state`, whether sharing with the specialist is allowed (A36: the referral hold reads it and `consent_updated` writes it, so it belongs to no single feature), and each milestone's clock (started, met, void);
- **messaging:** channel consent (`channel_sms`, the only channel the alphabet sends on), the open outreach holds, and the approved-unsent drafts (at most 2, because the alphabet has two approvable draft ids);
- **ownership:** the owner, and the pending handoff (provider, requester, owner it is from);
- **ladders:** the state's overdue flag, rung and acceptance; each milestone's overdue flag, rung and acceptance; and which ladders share one escalation event (coalescing, A19).

The following fields are left out: timestamps, event ids except the sharing pattern above, `backdated_events`, the message time lists, `consent_history`, the review and triage reasons, `reroute_exhausted_at`, `incomplete_procedure`, `closure_proposal`, `watchdog_flag`, `last_handoff`, `provider`, `referral_accepted_at` and `contact_confirmed_at`. `step` writes these fields for people and reports to read. It never reads them to decide acceptance or the next signature, except as described in the next paragraph.

**Why this is sound for I1 to I8.** Each invariant is a predicate on the event and on signature fields of the model before and after it: state, actor, payload, consent, holds, approved drafts. Suppose two concrete models with the same signature accept the same events and reach successors with the same signature. Then a search over one representative per signature visits every reachable signature and every transition between signatures. The predicates then hold for all concrete models exactly when they hold for the representatives. That premise is not assumed. Two checks test it on the run:

- **Abstraction check.** Up to 1,000 times per slice, the search reaches a concrete model whose signature it has already seen, by another path, and with different hidden fields. The checker then runs the whole alphabet on both models and requires the same accept/refuse result and the same successor signature for every event. A signature that leaves out something `step` reads is reported as `ABSTRACTION`. A test drops the holds from the signature and shows that the check catches it.
- **Replay (I6)** rebuilds every representative from its trace and requires equality on every field.

The weekly cap on automatic messages (`max_auto_messages_per_week`) is the only rule that reads a left-out field, `outbound_message_times`. It is enforced by the append-time guard, not by `step`, and the guard is outside this search (see "What it does not cover").

### Three slices

The full product of the four groups is too large for the time budget: a search over all of them grew about three times per BFS level and passed 25,000 states at depth 7. The features do not read each other's fields, so the search runs in three **slices**. Each slice has:

- the whole main path, closure, reopening, consent withdrawal and restoration, lost contact and ownership;
- the payload-only annotations;
- the events of its own feature, with every actor whose row holds them.

Each slice saturates on its own.

| Slice | Its own events | People in its own events |
|---|---|---|
| messaging | the consent ledger, `contact_updated`, every review reason, drafts, approvals and sends, callbacks | everyone; `accept_ownership` by the primary-care owner only |
| ownership | handoff request, accept, reject and bounce, `reassign`, the non-hold review reasons, sharing with the specialist refused and restored (A36) | everyone |
| ladders | `mark_overdue`, `escalate` (all forms), `escalation_accepted`, and the handoffs the milestones depend on | everyone for the ladder events; the referral by fixed people |

The claim that the slices do not interfere is also tested on the run, by the **non-interference check**. On representatives of each slice, the checker flips every field of the groups the slice does not vary: the other slices' fields. It then runs the slice's whole alphabet and requires the same accept/refuse result and the same successor, with the flipped group left out of the comparison. An event that reads another feature's fields is reported as `INTERFERENCE`. A test makes `close` read the outreach holds and shows that the ownership slice catches it.

Each slice uses a fixed cast of people for the events of the other features. Owner identity is read only by the handoff rules and by the route's owner scope, and every person is tried for those in the ownership slice. I5 and I7 apply the owner scope only in the ownership slice. The other slices check that their own fields never block a person. They take "the person who must own the case can come to own it" from the ownership slice, where it is checked with the owner scope on and every person in the cast.

### The actor table

The search tries each event type only with the actors whose row of the actor table (`ACTOR_EVENTS`, read at run time) holds it. `step` refuses every other pair before it reads the model: the agency, unknown actors, and a service with a type outside its row. The **table probe** checks this. On the first three representatives of every state name in every slice, it tries every such pair with every payload. Any acceptance is reported as `TABLE`. The exhaustive grid over every state is T-A51 (`api/tests/test_actor_table.py`).

## Results

Results of `make verify` at the commit of this ticket, on the development Mac:

| Slice | Abstract states | Accepted transitions | `step()` calls | Depth | Saturation | Abstraction pairs | Non-interference | Table probes | Replays |
|---|---|---|---|---|---|---|---|---|---|
| messaging | 4,736 | 209,328 | 1,430,272 | 17 | level 18 added nothing | 1,000 | 862 | 48,438 | 4,736 |
| ownership | 6,816 | 159,640 | 1,949,376 | 15 | level 16 added nothing | 1,000 | 802 | 44,310 | 6,816 |
| ladders | 12,178 | 248,458 | 3,653,400 | 21 | level 22 added nothing | 1,000 | 406 | 49,224 | 12,178 |
| **total** | **23,730** | 617,426 | 7,033,048 | | all saturated | | | | |

Notes on the table:

- **E20a F1** made `close` and `clinician_review` a physician's in the machine: the coordinator's and the supervisor's reviews and closes are now refused, so fewer transitions are accepted (629,954 before), and the abstract state count is unchanged (those edges led to states a physician also reaches).
- **Depth** is the length of the longest shortest trace from `case_created`. The saturation column names the BFS level that added no new state.
- **A36 (ADR-044)** added sharing with the specialist to the core signature and the `share-off` / `share-on` consent payloads, which doubles the messaging and ownership slices (17,954 states before); the ladders slice does not try them.
- **A32 (erasure)** added `erasure_completed`, `system:erasure`, the `erased` flag in the signature and I8; erased closed cases account for the growth from 17,071 states.
- **Time:** about 78 seconds after A36; about 60 to 75 seconds before it, while other work was running (36 to 50 seconds before A33 added the refusal path, the cancel event and the HL7 actor to the alphabet; the ladders slice keeps one counsellor form to stay small). The budget is three minutes.
- **States per level:** `make verify` prints the new states per level and the states per state name.
- **Gate cases:** the route gate refused 4,592 closes that the machine accepted with a foreign signer or a malformed reference (before A36; since ADR-044 the machine refuses the foreign signer itself and only the malformed references reach the gate). The append-time guard refused 104 unlisted templates that the machine accepted. In both cases the refusal comes from the layer that ADR-021 and ADR-032 put in charge.

### A real bug, found and fixed

The first run failed I3 with this counterexample:

```
1. message_approved   by primary_care_owner:...   {"draft_id": "d1"}
2. review_requested   by system:triage            {"reason": "opt_out", ...}
3. message_sent       by system:notifier          {"draft_id": "d1", "channel": "sms", ...}
```

The hold stopped only the automation's messages. In the failing sequence, a person approves a draft. The patient then replies "stop", or the reply shows the number is wrong. The outbox drains later: on a retry, or after a crash between the approval and the send (ADR-030). The approved draft still went out, to a patient who had opted out, or to a stranger holding the wrong number.

`_precheck` now refuses a notifier `message_sent` while any outreach hold is open. The outbox drain already treats a refusal as "the case changed between approval and send": the row is blocked and not retried. The person clears the hold (`consent_updated` for an opt-out, `contact_updated` for a wrong number) and then drafts again.

The regression tests are in `api/tests/test_model_check.py`:

- `test_an_approved_draft_is_not_sent_while_a_hold_is_open`, for both holds, with the send accepted once the hold is cleared;
- `test_the_outbox_drain_blocks_a_draft_approved_before_the_opt_out`, end to end through the outbox.

### Seeded mutations

Each mutation is applied with `monkeypatch` in `api/tests/test_model_check.py`, and each is caught with a counterexample.

| Mutation | Caught by | Counterexample (shortest) |
|---|---|---|
| M1: automation may close (`close` added to the automation's row, closure guard and, since E20a, the physician check blind to it) | I1 | the main path by people (7 events) or a recorded refusal, then `close` by `system:automation` |
| M2: evidence check removed (the close row's `requires` ignored) | I1 | the main path (7 events), then `close` with `evidence_ref` and no `signed_by` |
| M3: `message_sent` without approval (any draft id passes the gate) | I3 | one event: `message_sent` of an unapproved draft by the notifier |
| M4: the hold bug put back | I3 | the three events above |
| M5: consent freeze removed (`CONSENT_FROZEN_EVENTS` emptied) | I4 | `consent_updated {withdrawn: true}`, then `message_drafted` |
| M6 (A36): the machine's signer check removed (`_check_signer` a no-op) | I1 | a close or a signed review whose `signed_by` is a stranger's |
| M7 (A36): the referral hold removed (`referral_hold` always none) | I4 | a referral, a `consent_updated` that holds it, then a referral event that is accepted |
| M8 (E20a F1): the machine's physician check removed (`_check_physician` a no-op) | I1 | a `clinician_review` or `close` by the coordinator or the supervisor |

The checker's own checks are also tested. A signature that forgets the holds is caught as `ABSTRACTION`, and so is one that forgets the sharing flag (A36). An event that reads another slice's fields is caught as `INTERFERENCE`. A depth bound too small is reported as not saturated, which is a failure. A `step` that raises anything but `InvalidTransition` is reported as `CRASH`, with the trace.

### Observations (not violations)

Both observations below were answered by ADR-044 (A36), as owner-delegated defaults: the signer check is now in the closure guard (I1), and a referral is held while consent is withdrawn or sharing refused (I4). They are kept as the record of what the first run found.

- **The machine accepts a close signed by someone else.** ADR-021 deliberately puts the signer check (signer = actor, or `Practitioner/<actor's id>`) in the route, so the HTTP path refuses such a close with 403, and the checker shows that it does for every such close. An in-process caller that bypasses the route (a script, a future service) would not be stopped by the machine. The check is pure, and `step` has the actor, so it could move into the closure guard. The cost: the simulator's and some tests' synthetic signers would need to sign as the actor. This is recommended as a follow-up, not done here, because it reverses an accepted ADR.
- **A pending referral can be accepted while consent is withdrawn.** `handoff_requested` is frozen, but `handoff_accepted` of a request made before the withdrawal is not, so ownership can change while consent is withdrawn. No message goes out. Whether "handing the patient to someone new" (T-A23) should cover the acceptance is a question for the clinical lead.

## What it does not cover

- **Database concurrency, locking, idempotency and the stores.** Covered by the Postgres integration and chaos suites (`make test-db`, `make test-chaos`: T-A32, T-F3 to T-F7) and by the store tests. The checker drives `step` directly, one event at a time.
- **The HTTP layer as a whole:** authentication, RBAC on every route, PHI in URLs, rate limits and the full owner-scope paths. Covered by `make test-security` (T-F9 RBAC matrix, T-F10, T-F11) and the API tests. The checker uses the route's pure gates (`EVENT_POLICY`, `authorize_owner_scope`, `payloads.validate`, `_check_signature_and_evidence`) only for liveness and for the parts of I1 that are delegated to the route.
- **Closure evidence completeness in HAPI** (ADR-036: final report, complete colonoscopy). This needs the FHIR server. Covered by T-A67 (`api/tests/test_closure_completeness.py`). The checker covers the shape of the reference and the signer.
- **Time.** Working days, SLA due dates, the weekly cap on automatic messages and the same-working-day coalescing rule are computed by the sweep and the append-time guard from a calendar. The checker decides "due" by the payload. Covered by the sweep, calendar, coalescing and automation tests.
- **Channels other than SMS**, and more than two drafts. The machine treats channels and draft ids alike (lookups in the consent ledger and in the approved set). The checker uses one channel and two draft ids, plus an unknown channel and an unapproved id.
- **Other pathways.** Only the shipped YAML is checked. A changed pathway is checked by rerunning `make verify`, which is part of `make check`.

## How to rerun

```
make verify                                                        # every slice, every check (about 1 minute)
PYTHONPATH=api python tools/verify/model_check.py --slice ladders -v  # one slice, print each BFS level
PYTHONPATH=api python tools/verify/model_check.py --all --json        # first violation of every slice, as JSON
PYTHONPATH=api .venv/bin/pytest api/tests/test_model_check.py -q      # the bug's regression tests and the mutations
```

`--max-depth` bounds the search. Hitting the bound with states still being found is a failure (`saturated: FALSE`), never a silent pass. `--abstraction-samples` and `--interference-samples` set how many pairs the two soundness checks compare.
