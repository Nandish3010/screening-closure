# The second pathway: cervical screening on the same engine

Status: implemented and tested on the engine (ticket A37, ADR-050). **Not clinically reviewed, not simulated, no outcome, impact or labour number claimed.** This page says what exists, what is data and what is code, which timings are sourced and which are placeholders, and what a reader must not infer.

## Which pathway, and why

Cervical screening: an abnormal HPV or cytology screening result, a referral, a colposcopy, a documented closure. Breast screening (an abnormal mammogram, then assessment or biopsy) was the alternative.

| | Cervical (chosen) | Breast |
|---|---|---|
| Target of the follow-up | One procedure, the colposcopy | An assessment or a biopsy: two procedures and a choice |
| Engine change | None to the state machine; the closure evidence rule is a pack setting | The "target procedure" would need a set, not one value |
| Sourced in the repository | The DoH cervical cancer screening standard V2 (October 2025) is named in [buyer-map.md](../commercial/buyer-map.md) with "V2 texts not read"; volumes are assumed ([tools/pilot/inputs.yaml](../../tools/pilot/inputs.yaml)) | The same, for the breast standard V2 |

Neither is better sourced, so the choice rests on engine fit: cervical needed the least. The IFHAS standard V10 (July 2025, [evidence.md](../evidence.md) section 8a) is the only timing source in the repository that is not specific to the colorectal programme.

## What is data and what is code

**The pack** ([`config/pathways/cervical_hpv.yaml`](../../config/pathways/cervical_hpv.yaml)) `extends` the bowel pack. It sets only these top-level keys; everything else is the bowel pack's, literally:

| Key | What it holds |
|---|---|
| `pathway`, `version`, `description`, `labels` | Name, version, a one-line description, the English and Arabic label |
| `slas_working_days` | The per-state SLAs |
| `milestones` | The two cross-state clocks |
| `evidence` | The confirmatory procedure (`colposcopy`), what may close a case, whether the record must name the procedure, the codes a patient-level search may use (none) |
| `patient_messages.next_step` | What the shared patient messages call the next step, in English and Arabic |

Inherited unchanged: the nine states and five side states; every transition, including the closure row (`close` requires `evidence_ref` and `signed_by`); the annotations; the human-only events; the escalation ladder; the handoff contract; the informed-refusal path. The confirmatory events keep their generic names (`book_confirmatory`, `complete_confirmatory`); for this pathway the confirmatory procedure is the colposcopy. The physician-signer-is-the-closer rule, the consent hold and default-deny live in the state machine and the actor table, which this pathway does not touch.

**Engine changes**, all generic (no `if pathway == ...` branch anywhere; `test_t_sp2` greps for one):

1. `app.pathway`: `extends` (one level of inheritance, same directory, still validated by the same closure-guard check), `EvidenceRule`, `PathwayRegistry`, `merge_counts`.
2. The case row already named its pathway; every case-scoped route now steps the case with that pack (`routers.cases.pathway_of`). `POST /cases` takes an optional `pathway` (default: the first loaded, bowel screening). Stores gained `pathway_name_of`.
3. The worklist is one page over every loaded pathway in case-id order; the sweep, the automation scan, the evidence scan and the watchdog scan run once per pathway and add up their counts. The FHIR projector, the erasure service, the HL7 handler and the agency resolve the pathway per case.
4. `app.evidence`: `refusal_reason(assessment, rule)` applies the pack's rule; the verifier and the finder take the procedure and the search codes; the recorder (`app.confirmatory`) writes the pathway's procedure.
5. `tools/verify/model_check.py` explores any pack (`--pathway`, `--all-pathways`); `make verify` explores both.
6. Configuration: `PATHWAY_PATHS` (compose sets the cervical pack), `config/providers.yaml` (specialty `gynaecology`, synthetic provider names).

With one pathway loaded, every route, sweep and scan answers exactly as before: the bowel pack's events, closure rule and flags are untouched (ADR-050).

## Timings: sourced or placeholder

| Timing | Value | Status |
|---|---|---|
| Referral requested after the result is released | 15 working days | **Sourced:** DoH IFHAS standard V10 (July 2025), referral to secondary care within 15 working days of releasing results ([evidence.md](../evidence.md) section 8a, read first-hand). The clause is written for the IFHAS programme in general; whether it governs a cervical result is for H1 to confirm. |
| Colposcopy after the referral request | 31 working days | **Placeholder, needs clinician sign-off (H1).** It is the colorectal colonoscopy figure (National Guideline Appendix 2) carried over so the clock runs. It is not a cervical standard. The repository holds no cervical timeline. |
| Per-state SLAs (acknowledge 2, accept ownership 1, contact the patient 3, patient acknowledges 5, book 5, review 5, close 3, re-route 2) | the bowel pack's values | **Placeholder, needs clinician sign-off (H1).** They are design values, not sourced, and not specific to this pathway. |
| Escalation offsets (0, 3 and 7 days after a breach), handoff window (3 working days) | inherited | Design values, as for the bowel pathway (H1). |
| Re-offer interval after an informed refusal | 12 months, inherited | Assumed (H-11 is open). |

The case page shows the pathway badge, words the colposcopy clock "(placeholder, H1)" and says the timing is carried over from the colorectal pathway.

## Patient messages

The message templates (`api/app/ai/templates/en.yaml`, `ar.yaml`) are shared by both pathways. They state no finding and name only "the next step", through the existing placeholders `{owner_name}`, `{next_step}` and `{appointment_date}`. The cervical pack supplies `{next_step}` as an appointment, never a finding (E14): English "a follow-up examination", Arabic "فحص متابعة في العيادة". `test_t_sp12` renders every purpose on every channel, with and without a date, in both languages, and runs each text through the validator (forbidden phrases, placeholders, length, language, digits). **The English wording needs a clinician's review (H1); the Arabic needs native-speaker review (H2)**, like every existing Arabic string. Model drafts for a cervical case go through the same validator and the same human approval gate; the recorded Claude outputs hold no cervical prompt, so a draft falls back to the template (abstention reason `cache_miss`) in the offline demo.

## Closure evidence

A cervical case closes only on a final `DiagnosticReport` that states a **complete colposcopy**, or a completed `Procedure` linked to one, that belongs to the case and **names the colposcopy**, with a physician's own signature. A complete colonoscopy of the same patient, an incomplete or preliminary report, a CT-colonography report and a record with no completeness statement are all refused (422, nothing written). No SNOMED or LOINC code is asserted for the colposcopy (none is cited in the repository), so the evidence scan finds a case's record by its case identifier only. What counts as a complete colposcopy (adequacy, the transformation zone seen) is a clinical definition this pack does not make (H1).

## Tests

| Test | Where |
|---|---|
| The pack is data; no pathway name in the engine; timings sourced or marked; the evidence rule; loader refusals | `api/tests/test_second_pathway.py` (T-A83) |
| Result to closed through the API with evidence; refusal without valid evidence; physician and signer; default-deny; consent hold | the same file (T-A84, T-A85) |
| Every message, both languages, through the validator | the same file (T-A86) |
| The model checker on the cervical pack: I1 to I8, saturation, state counts, a seeded mutation | `api/tests/test_model_check.py` (T-A87) and `make verify` |
| The simulator's sweep conformance on the cervical pack | `simulator/tests/test_sweep_conformance.py` (T-A88) |
| The demo seed | `simulator/tests/test_create_cervical_cases.py` (T-A89) |
| The worklist and case page for both pathways | `web/tests/pathways.test.tsx` (T-C19) |

Model checker (`make verify`, state counts per pathway): MODEL_CHECK_COUNTS

The sweep-conformance run on the cervical pack uses the bowel programme's cohort and behaviour parameters. It checks that the simulator's log equals the real store's and sweep's, event for event. It is not an evaluation, and none of its output is reported anywhere.

## Demo

`make demo-seed-cervical` (or `make demo-seed DEMO_SECOND_PATHWAY=1`) creates four synthetic cervical cases at different stages on the running stack. It is opt-in and kept out of the demo snapshot and the video: the storyboard says "all 22 open cases" and finds its cases by state (`owner_accepted`, `confirmatory_completed`), none of which a cervical demo case ever occupies; `make demo-reset` removes them. The web mock shows both pathways with `?pathways=2`.

## What not to infer

- No health, labour, cost or outcome number exists for this pathway. The simulator was not run to evaluate it; the closure and effort metrics, the risk score and the fairness watch's metrics stay bowel-only.
- "Implemented and tested on the engine" is not "clinically reviewed". The pathway, its wording and its timings have not been seen by a clinician (H1) or a native Arabic speaker (H2).
- The blood-based colorectal and the breast recall pathways named in the business case are designed only. Nothing of them is built.
- The Helm chart and the Kubernetes scale runs load the bowel pack only; the scale evidence is for that pathway.

## Hazards

HZ-47 to HZ-50 in [hazard-log.md](hazard-log.md).
