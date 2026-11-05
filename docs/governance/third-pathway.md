# The third pathway: long-term conditions, and result priority tiers

Status: implemented and tested on the engine (ticket A38, ADR-051). **Not clinically reviewed, not simulated, no outcome, impact or labour number claimed.** Every clinical threshold and timing in the pack is a placeholder pending a clinician (H1); the Arabic awaits a native reader (H2). This page says what exists, what is data and what is code, which numbers are sourced and which are placeholders, and what a reader must not infer.

## Why this pathway

The challenge owner (the Abu Dhabi Public Health Centre's head of screening) described the loop as covering any actionable result of the comprehensive adult screening programme (IFHAS, ages 18 to 75), not only cancer: record the result, identify and prioritise people at higher risk by agreed criteria (for example hypertension, obesity, diabetes or cancer), refer to the appropriate service, send reminders, track the outcome, and link flagged cases back to the person's primary care or family physician. The bowel and cervical pathways cover the cancer part. This one covers the cardiometabolic part: an abnormal blood pressure, HbA1c or fasting glucose, lipid or BMI result goes to the **family physician**, and the case closes when **the visit happened and a care plan is recorded**.

## What is data and what is code

**The pack** ([`config/pathways/chronic_conditions.yaml`](../../config/pathways/chronic_conditions.yaml)) `extends` the bowel pack, as the cervical pack does, and sets only these top-level keys:

| Key | What it holds |
|---|---|
| `pathway`, `version`, `description`, `labels` | Name, version, one line, the English and Arabic label ("Long-term conditions") |
| `slas_working_days`, `milestones` | The per-state SLAs and the two cross-state clocks |
| `evidence` | The confirmatory procedure `care plan visit`, only `complete` may close, the record must name it, no search code |
| `patient_messages.next_step` | "a visit with your family doctor" and its Arabic |
| `priority` | The tiers, the result kinds and the rules (below) |

Everything else is the bowel pack's, literally: the states and side states, every transition including the closure row, the annotations, the human-only events, the escalation ladder, the handoff contract and the informed-refusal path. The generic event names stay: `handoff_requested` is the referral to the family physician, `book_confirmatory` books the visit, `complete_confirmatory` records that it happened. Clinician-signed closure (a physician who signs as themselves, with evidence), default-deny and the consent hold apply unchanged.

**Engine changes** (all generic; no pathway name and no clinical number in the engine, and `test_t_cp2` greps for both):

1. `app.pathway`: the `priority` section (`PriorityConfig`, `PriorityRule`), validated strictly at load.
2. `app.priority` (new): derives a tier from results with any pack's rules; describes a recorded tier for staff.
3. `POST /cases` takes optional `results`; the case row and case page carry `priority`.
4. The genesis payload may carry `priority` and `priority_rule` (codes), set by the route only.
5. The recorder slugs a multi-word procedure label in resource ids (`Procedure/care-plan-visit-<case id>`); one-word labels are unchanged.
6. Packs' procedure labels are registered so report text naming them is recognised; `config/providers.yaml` maps the pathway to a family-medicine reroute list (synthetic names); compose loads the pack.

## Sourced and placeholder numbers

The repository cites **no** cardiometabolic standard: no blood pressure, HbA1c, glucose, lipid or BMI threshold, and no primary-care follow-up timeline. So:

| Item | Value | Status |
|---|---|---|
| Referral requested after the result is released | 15 working days | **Sourced:** DoH IFHAS standard V10 (July 2025), referral within 15 working days of releasing results ([evidence.md](../evidence.md) section 8a). The clause is written for referral to secondary care; whether it governs a referral to primary care is for H1. |
| Family physician visit after the referral request | 20 working days | **PLACEHOLDER, needs clinician sign-off (H1).** A design value; no source. |
| Per-state SLAs, escalation offsets, handoff window | the bowel pack's | **PLACEHOLDER, needs clinician sign-off (H1)**, as in the cervical pack. |
| What counts as a documented care plan | not defined | **H1.** "Complete" means the visit happened and a plan is documented; the pack reads a report's own statement. |
| Every priority tier and threshold | see below | **PLACEHOLDER, needs clinician sign-off (H1).** None is a guideline value; none may be quoted as one. |

## Priority tiers

A pack's `priority` section names the tiers highest first (`urgent`, `soon`, `routine`), the result kinds with their fields (the unit is in the field name), and rules. A rule matches one kind when `any` or `all` of its conditions (`gte`, `gt`, `lte`, `lt`, `eq` against a number) hold, and carries a `reason` in both languages and a mandatory `basis`. A case takes the highest tier any rule gives any of its results (the first listed rule breaks a tie); results that match nothing get the pack's default tier with the rule id `no_rule_matched`.

Placeholder rules in the chronic pack (all marked in the file): very high blood pressure is `urgent`; high blood pressure, a high HbA1c and a high fasting glucose are `soon`; a mildly raised HbA1c, raised blood pressure, fasting glucose, LDL cholesterol or BMI are `routine`. The cervical pack has one placeholder rule: a positive cancer screening result is `urgent`. **The bowel pack file is not edited**, because the simulator results quote its hash; a bowel case therefore has no priority yet (ADR-051 says what the follow-up is).

**What is recorded.** At intake the route derives the tier and records only `priority` and `priority_rule` on the genesis event. The values are not stored. The reason shown to staff is the rule's wording, looked up by id.

**Where it shows.** The API (`priority` on every staff case row and case page: tier, rank, rule, reason in English and Arabic, basis); the web worklist (a badge, and the sort: tier first, then overdue, days overdue, risk, age; a case with no priority sorts like the lowest tier); the case page (the badge, the reason, and the statement that it does not change the SLA clock). A patient never sees it.

**What it does not do.** It orders and explains. It changes no state, SLA clock, escalation or closure. **The SLA clock does not depend on the tier**: due dates come from the pack's per-event days and milestones by replay, and making them tier-dependent would touch the sweep, the read model, the next-due index and the model checker's timing abstraction for numbers no one has validated. It is displayed and sorted by only.

## Patient messages

The shared English and Arabic templates are used through `{owner_name}`, `{next_step}` and `{appointment_date}`. The pack supplies the next step as a visit, never a finding or a tier. `test_t_cp6` renders every purpose on every channel, with and without a date, in both languages, through the validator. The English needs a clinician's review (H1); the Arabic, including the Arabic reason texts of the priority rules, needs native-speaker review (H2). The recorded Claude outputs hold no prompt for this pathway, so a model draft falls back to the template in the offline demo.

## Closure evidence

A case closes only on a final `DiagnosticReport` that states a **complete care plan visit**, or a completed `Procedure` linked to one, that belongs to the case and names the `care plan visit`, with a physician's own signature. A colonoscopy or colposcopy of the same patient, an incomplete or preliminary report and a record that says nothing about completeness are refused (422, nothing written). No terminology code is asserted.

## Tests

| Test | Where |
|---|---|
| Pack is data; engine names no pathway or number; every number sourced or marked; evidence rule | `api/tests/test_third_pathway.py` (T-A90) |
| Result to closed through the API; refusals; physician and signer; default-deny; consent hold; the recorder | the same file (T-A91) |
| Messages in both languages; coexistence of three pathways | the same file (T-A92) |
| Model checker on the pack: I1 to I8, saturation, counts, seeded mutation; sweep conformance | `api/tests/test_model_check.py`, `simulator/tests/test_sweep_conformance.py` (T-A93), `make verify` |
| Derivation, loader refusals | `api/tests/test_priority.py` (T-A94) |
| Intake, API, patient redaction, no clock or decision path | the same file (T-A95) |
| Opt-in demo seed | `simulator/tests/test_create_chronic_cases.py` (T-A96) |
| Badge, sort, case page, third pathway in the mock | `web/tests/priority.test.tsx` (T-C20) |

Model checker (state counts for the pack on its own): 23,730 abstract states (messaging 4,736, ownership 6,816, ladders 12,178), all slices saturated, I1 to I8 hold. The count equals the bowel and cervical packs' because the pack changes timings, evidence, wording and priority rules, none of which the abstraction reads; it is explored in full, nothing is assumed from its parent. `make verify` now explores three packs (71,190 states in total).

## Demo

`make demo-seed-chronic` (or `make demo-seed DEMO_THIRD_PATHWAY=1`) writes four SYNTHETIC patients with Emirati-style names to HAPI (fixed ids, tagged SYNTHETIC, no identifier) and creates one case each: very high blood pressure twice (urgent, left at `acknowledged` so both are on the coordinator's Today screen), a high HbA1c (soon, `patient_contacted`) and a raised BMI (routine, `patient_acknowledged`). **They are part of the demo snapshot** (`make demo-snapshot` runs the seed, and so does `deploy/gcp/vm/seed-on-vm.sh` before it saves the hosted demo's snapshot), so `make demo-reset` brings them back (ADR-057). The Today screen shows each with a plain-language reason ("Very high blood pressure") and a visible chip that the threshold is a placeholder pending clinician sign-off (H1). Its stages never include `owner_accepted` or `confirmatory_completed`, the states `tools/video/record.ts` finds its cases by. The web mock shows them with `?pathways=3`.

## What not to infer

- No health, labour, cost or outcome number exists for this pathway; the simulator was not run on it for evaluation. The closure and effort metrics, the risk score and the fairness watch stay bowel-only.
- "Implemented and tested on the engine" is not "clinically reviewed". Tiers, thresholds, wording and timings have not been seen by a clinician (H1) or a native Arabic speaker (H2).
- The tier is not a diagnosis, not a risk score and not a clinical priority; it is a placeholder ordering aid.
- The Helm chart and the scale runs load the bowel pack only.

## Hazards

HZ-51 to HZ-55 in [hazard-log.md](hazard-log.md).
