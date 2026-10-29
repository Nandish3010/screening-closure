# Data protection impact assessment (DPIA)

Status: **draft for human review (E18, T-E21).** Sign-off by the data protection officer and the clinical lead is pending (section 12). This is a design-time assessment of a prototype that holds synthetic data only ([data-provenance](../data-provenance.md)). It is not legal advice, not a compliance claim and not a substitute for the assessment the controller must make before real patient data is processed. It says what the system would process in a pilot, what could go wrong for the people whose data it is, what is built to stop that, and what is still open.

Structure. The UAE Federal Decree-Law No. 45 of 2021 (PDPL) has an impact-assessment duty; one secondary source places it in Article 21, which was **not** checked against the official text (to verify, see [regulatory-mapping.md](regulatory-mapping.md) item 1). The PDPL's content requirements for an assessment are not stated in any source read, so the headings below follow GDPR Article 35(7) practice (systematic description, necessity and proportionality, risks to individuals, measures) as a **reference model**, not as a legal requirement. GDPR does not apply here by itself.

Related documents: [regulatory-mapping.md](regulatory-mapping.md) (the legal readings and what is unverified), [adhics-controls.md](adhics-controls.md) (control-by-control table), [erasure.md](erasure.md) (the 18 places personal data can live and the erasure procedure), [hazard-log.md](hazard-log.md) (the risks in the form of hazards), [ai-boundary.md](ai-boundary.md), [reply-triage-card.md](reply-triage-card.md), [risk-score-card.md](risk-score-card.md), [identity.md](identity.md), [rbac-matrix.md](rbac-matrix.md), [operations.md](operations.md), [red-team.md](red-team.md), [hl7-adapter.md](hl7-adapter.md), [assumptions-limitations.md](../assumptions-limitations.md).

Reading rules. "Built" means the code is in the repository and a test cites it. "Verified" and "to verify" follow the rules at the top of regulatory-mapping.md. Test ids are in [TEST-CASES.md](../../project/TEST-CASES.md); decisions are in [DECISIONS.md](../../project/DECISIONS.md).

## 1. Summary

| Question | Answer |
|---|---|
| Is a DPIA needed? | Yes if the system ever handles real data. It would process health data of people in a screening programme at scale, uses a language model for patient-facing drafts, a small model to read patient replies, and a regression to order a worklist. Each of these would be a trigger under GDPR Art. 35(3) practice, and the PDPL's own high-risk cases (large volumes of sensitive data, new technology) point the same way (secondary sources, to verify). |
| What is processed today? | Synthetic data only. No real person is in the repository, the tests or the demo. |
| Role in a pilot | The screening provider (or programme) is expected to be the **controller**; this service would be a **processor** acting on its instructions. Both are **to verify** with the provider and the regulator; nothing is agreed. |
| Biggest unresolved questions | (1) Whether PDPL applies to health data or the health-sector laws govern it. (2) The retention duty (Federal Law No. 2 of 2019, reported at 25 years) against erasure. (3) Whether any model or cloud service can be used for real data at all (ADHICS CM 4.2, localisation). (4) Backups. |
| Residual risk today | Low for the synthetic prototype. **Not acceptable for real data until the items in section 11 are closed**; the highest ones are DR-01, DR-03, DR-06 and DR-02. |

## 2. Description of the processing

### 2.1 Purpose

One purpose: **tracking and closing the follow-up of an abnormal colorectal screening result** (a positive FIT) until the patient has had a diagnostic colonoscopy, a documented informed refusal, or another safe exit, signed by a clinician. The system moves nothing clinical: a deterministic state machine decides transitions, humans approve messages and sign closures (ADR-001, ADR-007, [ai-boundary.md](ai-boundary.md)). The risk score is used only to order a worklist (T-D10).

### 2.2 Who is involved

| Party | Role in the processing | Basis for the role |
|---|---|---|
| Patient (a person aged 40 to 75 with a positive FIT) | Data subject; receives messages, may reply, may withdraw consent | The programme |
| Screening provider or programme | Controller (to verify); sets purpose, lawful basis, retention | Pilot agreement, not yet in place |
| This service (the operator of the closure software) | Processor (to verify); hosts and runs the software | Pilot agreement and processing terms, not yet in place |
| Clinicians (primary care and specialist owners), coordinators, supervisors, lab | Users with six roles; staff are also data subjects (names, actions) | [rbac-matrix.md](rbac-matrix.md) |
| Hosting provider | Sub-processor (to verify); must be in the UAE | ADR-026 |
| Model provider (drafting, planning) | Optional sub-processor; off by default | section 6.3 |
| Malaffi (HIE operator) | Possible source of messages; not connected | [malaffi-adapter-plan.md](malaffi-adapter-plan.md) |

### 2.3 Scale (design assumption, not a measurement)

The cost model assumes up to 200,000 screened members a year and a FIT positivity of 0.07, which is about 14,000 positive cases a year, a labelled estimate in [pilot-hardening.md](pilot-hardening.md). A pilot is far smaller. The scale matters for the DPIA test of "large-scale" processing, which is to verify with the regulator.

### 2.4 Nature of the processing

Receive an abnormal result (HL7 v2 ORU or FHIR, [hl7-adapter.md](hl7-adapter.md)); open a case with an opaque patient reference; assign an owner; run SLA timers and an escalation ladder; draft patient messages (template by default; a model only if switched on) for the owner's approval; send approved messages or pre-approved templates (a mock in the prototype); classify patient replies with a local model and route unsure ones to a person; keep a consent ledger; record confirmatory evidence; close with a clinician's signature; erase on request.

## 3. Data inventory

The authoritative inventory of where **personal data can sit** is the 18-row table in [erasure.md](erasure.md) section 1. This table groups the same places by data category. The row numbers in the last column are erasure.md's.

| Category | Examples | Sensitivity | Where it is held (erasure.md row) | Erasable today |
|---|---|---|---|---|
| Direct identifiers | Name, MRN, phone, address, birth date | Personal | HAPI `Patient` (1), HL7 contact registry (13) | Yes (A32) |
| Health data | Positive FIT result, colonoscopy report, procedure, free-text notes and findings | Health data (special) | HAPI compartment resources (2), projected Task, ServiceRequest, Appointment, Communication, Consent, Provenance (3), history (5) | Yes (A32) |
| Pathway data | Case state, SLA flags, owner role, event types and times, evidence references | Health-related, pseudonymous | `cases`, `events` (6, partly), kept after erasure with a random token | Reference replaced; event shape kept |
| Message text | Drafts, prompts, notifications as sent | Personal (names the care owner, says a result is ready) | `drafts` (7), `prompts` (8), `notifications` (9) | Tombstoned (A32) |
| Patient replies | Free text from the patient, intent label | May contain anything, including health information | `patient_replies` (10) | Tombstoned (A32) |
| Consent records | Per-channel and data-sharing booleans, history | Personal | `events` (consent events), HAPI `Consent` (3) | Reference replaced |
| Agent work | Work packs, outputs, resolutions | Pseudonymous; no patient text reaches agents (T-A66) | `agency_tasks` (11), `outbox`, queue errors (12) | Yes (A32) |
| Staff data | Display names, role, opaque actor id, actions | Personal (staff) | `users` (14), HAPI `Practitioner` and `Device` (4), actors in the log | **No** (out of scope of A32) |
| Registers and logs | Erasure register, access and service logs | Opaque ids only (T-F10, T-F11) | `erasures` and `erasure_map` (15), logs (16) | Register kept; logs not rewritten |
| Copies | Database backups and replicas; browser caches | Everything above | (17), (18) | **No** |

Not collected by design: Emirates ID or any national identifier in tokens, logs, the `users` table or the event log ([identity.md](identity.md)); clinical history in model prompts (the model sees the care owner's name, the next step, a date, the pathway name); production data for model training (DoH Responsible AI Standard 3.1.4.4.2 as recorded in regulatory-mapping.md; the risk score and the reply model are trained on synthetic data only).

## 4. Lawful basis and consent model

**Status: not settled.** The lawful basis for running a screening follow-up programme is the provider's decision (to verify). This section records the model the software supports and what it does not decide.

| Question | What the system does | What is open |
|---|---|---|
| Basis for tracking an abnormal result | Treated as part of care delivery and the screening programme the patient is already in. The software does not claim a basis. | Whether the basis is consent, performance of a health service, a legal or public-interest duty, or another basis, under PDPL and the health laws (to verify). Secondary sources disagree on legitimate interests under PDPL. |
| Consent to contact | A consent ledger records consent per channel (SMS, WhatsApp, phone, letter) and for data sharing as an append-only history; the current value is derived by replay (A6, ADR-017, T-A24). Withdrawal freezes outreach and flags the case for a human (T-A23, T-F7). The send-time check is in the notification path (A7, ADR-018, T-A25); an automatic template needs consent when it is written (ADR-032, T-A43). | Who records consent and how it is evidenced at the point of screening. A coordinator can record consent on a patient's instruction (L29), which needs an evidence rule. |
| Withdrawal by reply | An `opt_out` reply always goes to a person and opens an outreach hold for the automation; the person records the withdrawal (reply-triage-card.md section 6, HZ-25). | The model can miss an opt-out (section 9, DR-09). |
| Special-category (health) data | Processed for the purpose above only, with role-limited access (section 8). | Whether explicit consent is also needed for health data under PDPL, or whether health data is outside PDPL (regulatory-mapping.md item 1). |
| Data sharing with third parties | Consent ledger has a data-sharing flag. No outbound path to Malaffi or a provider EMR is built ([hl7-adapter.md](hl7-adapter.md) "Not built"). | Malaffi consent and opt-out rules (regulatory-mapping.md section 5, to verify). |
| Language | Arabic and English; Arabic wording unreviewed (H2, L17). | A patient may not understand the notice or the consent. |

Notice to patients. Messages say the result is ready and the care team will talk to the patient (E14); the patient page states that the message is automated assistance reviewed by the care team. A full privacy notice (who the controller is, purposes, rights, contacts, retention) is **not written**; it belongs to the provider.

## 5. Necessity and proportionality

| Principle | Assessment | Evidence |
|---|---|---|
| Purpose limitation | One purpose. The score orders a worklist and decides nothing. Agents have no tool that decides. | ADR-007, T-D10, T-A61 |
| Data minimisation | Case records carry identifiers, state and times, not the clinical record. Event payloads are strict per-type schemas that reject free text and unlisted keys. Prompts carry no name, contact or history. Agents never see patient text. Negatives are not stored by the simulator. | A14, ADR-019, T-A36, T-A66, T-D8 |
| Could the purpose be met with less? | A template-only system with no model and no reply triage meets the purpose; that is the default (`LLM_PROVIDER=template`, `TRIAGE_ENABLED` can be off). The model uses are optional conveniences. A pilot should start with them off or in shadow mode. | ADR-007, reply-triage-card.md section 8 |
| Accuracy | Corrections are new events; contact validity can be updated by an HL7 ADT message; the wrong-number flag exists. No tool lets a patient correct their own record. | HZ-14, HZ-02, hl7-adapter.md |
| Storage limitation | **Weak.** No retention schedule. The log is kept as the audit trail. Erasure exists but is a manual, two-supervisor act and is refused for open cases. | erasure.md section 6, HZ-34 |
| Integrity and confidentiality | Append-only log with triggers, least-privilege database role, default-deny roles, token verification, no PHI in logs or URLs. | ADR-004, ADR-029, ADR-040, T-A8, T-F9, T-F10, T-F11 |
| Transparency | Provenance for every draft (prompt, model, approver, time); event log; timeline per case. Patient-facing notice not written. | T-D5, T-A28 |
| Accountability | Hazard log, decision matrix, red-team suite. DPO not appointed. | E5, E6, D14 |

Judgement. The collection is proportionate to the purpose **if** the retention duty is settled and the model uses stay off or in shadow until validated on real data. The one proportionality weakness the design cannot fix alone is storage limitation.

## 6. Data flows

### 6.1 Flow table

| # | From to | Data | Channel | Prototype | Pilot or production |
|---|---|---|---|---|---|
| F1 | Lab or Malaffi to API | Abnormal result, patient identifier, times | HL7 v2 over HTTP (`POST /hl7`, bearer token) or FHIR | Synthetic, local | Needs TLS, VPN or gateway agreed with the operator (not built; hl7-adapter.md) |
| F2 | API to Postgres and HAPI | Pathway events; FHIR resources | Internal | Local compose, ports bound to 127.0.0.1 | In-country, separate servers for case store and FHIR store (L20) |
| F3 | Staff browser to API | Case data by role | HTTPS (production), OIDC sign-in | Local | UAE PASS or the provider's IdP (not integrated) |
| F4 | API to patient | Approved message text | Notification mock writes to a log | No delivery | A provider-approved channel (L26); the message carries no result (E14) |
| F5 | Patient to API | Reply text | Coordinator keys it in (`POST /cases/{id}/replies`) | Synthetic | A gateway token is not built |
| F6 | API to reply classifier | Reply text | Local worker process on the same host | No network | Same host; no network call at serve time |
| F7 | API to drafting provider | Prompt: owner name, next step, date, pathway | Off by default (`LLM_NETWORK_ENABLED=false`) | Template or recorded cache | See 6.3 |
| F8 | API to agency planner model | Candidate task labels, case ids, no text | Same provider switch as F7 | Template/rules | See 6.3 |
| F9 | Operator to erasure | Patient reference in a request body | HTTPS, never in a URL | Local | Same |

### 6.2 What does not leave the host

The reply classifier (Qwen2.5-1.5B with a LoRA adapter) runs on the same machine as the API. This is the one place free patient text meets a model, and it makes no network call. The risk score is a local regression. The notification path is a mock. Nothing in the repository sends data outside the host unless a person sets `LLM_PROVIDER` to an HTTP provider **and** `LLM_NETWORK_ENABLED=true` (T-D4, T-D6, T-D7).

### 6.3 The model API path, including the Claude API

| Item | State |
|---|---|
| Adapters in the code | `azure_openai` and `jais` (`api/app/ai/adapters.py`), network off by default, https only, key never logged, prompt without patient data (ai-boundary.md section 3). **There is no Anthropic or Claude adapter in the code.** |
| If the Claude API (or any hosted model API) were used for drafting or planning | It would be a new adapter behind the same interface, with the same prompt (care owner's name, next step, date) and the same validator, human approval and template fallback. The prompt still contains a staff name and information that a person is in a screening programme (the pathway name), which is personal data once tied to a patient in the provider's records. |
| Prototype | Synthetic data only. Any hosted model used in development, including by the developers' tools, sees only synthetic data and the source code. No real patient data was or may be sent. |
| Pilot and production | **A hosted model API outside the UAE is not an option for real data.** Federal Law No. 2 of 2019 (secondary reading: Art. 13) bars storing, processing, generating or transferring health data outside the UAE without approval; ADHICS v0.9 CM 4.2 bars cloud use for health information unless DoH authorises it; the Responsible AI Standard (3.1.4.5.1) requires compute, storage and disaster recovery in the UAE and remote support from within the UAE. ADR-026 sets the order: the provider's own environment, then a DoH-authorised UAE cloud, otherwise the **template fallback**. A model route for real data needs an in-country, DoH-accepted endpoint, a processing agreement with no retention or training on the prompts, and a written assessment of whether the prompt is health data. Whether any Claude-compatible in-country endpoint exists is **to verify**; none is assumed. |
| Residual position | Safe by default: with no model route the product works unchanged (ADR-007). Risk DR-06 below. |

## 7. Retention

| Item | State |
|---|---|
| Health-record minimum | Secondary sources (Latham and Watkins, Mondaq, Global Compliance News, found 2026-10-25) read Federal Law No. 2 of 2019 Article 20 as a minimum of **25 years** from the last health procedure or the last contact (they differ on the trigger), and longer if needed. The instrument and the Executive Regulations were **not read**. regulatory-mapping.md says "period not stated in the sources read"; this DPIA adds the figure as **secondary and unverified**. |
| Conflict | If the duty applies to the data this system holds, then erasing the clinical resources on a data-subject request (A32) may be unlawful before the period ends, and keeping a case log for 25 years is not minimisation. The two obligations cannot both be met in full for the clinical record. |
| Which data is the health record? | Unclear. The authoritative clinical record belongs to the provider's EMR. This system holds a **copy** in HAPI and a workflow log. A defensible position is that the closure system is not the record of care and may erase its copy (the EMR keeps the record), but that is a legal opinion, **to verify** with the DPO and DoH. |
| What the code does | Nothing automatic. No expiry job. `retention_period_ended` is only a label a supervisor picks for an erasure; the code does not check any period (erasure.md section 6). |
| Needed before real data | A written retention schedule per category (HAPI copy, event log, message text, reply text, prompts, register, logs, backups, staff data); a decision on which system is the record; a rule for refusing or limiting an erasure request during the period; and a deletion job at the end. Not built, no ticket. Hazard HZ-34, new hazard HZ-41. |

## 8. Data subject rights

| Right | Where it comes from | State | Evidence | Gap |
|---|---|---|---|---|
| Information (notice) | PDPL, GDPR practice | Not built | none | Privacy notice owned by the provider |
| Access and copy | PDPL (secondary) | **Partly** | `GET /cases/{id}/timeline` lists everything the event log holds about a case (A9, T-A28) | Not a complete export: HAPI resources, drafts, notifications and reply texts are not in it; no patient-facing request route; the patient sees only their own case page |
| Rectification | PDPL (secondary) | Partly, by design | Corrections are new events that supersede earlier ones; contact validity via HL7 ADT; wrong-number flag | No self-service correction; original value stays in the log (opaque); in HAPI the source system should correct and resend |
| Erasure | PDPL (secondary) | **Built** | `POST /erasures` and `POST /erasures/{id}/approve`, two different supervisors, fixed reason list, HAPI delete and expunge verified 404, text tombstoned, reference replaced by a random token, audit trail kept (A32, ADR-025, T-A79, [erasure.md](erasure.md)) | Only closed cases; backups and replicas; logs not rewritten; staff data; retention conflict (section 7); a zero-hit scan covers the inventory only |
| Restriction or suspension | PDPL (secondary) | Partly | Consent withdrawal freezes outreach (T-A23, T-F7); `opt_out` raises an outreach hold | No general "restrict processing" switch; the case stays on the worklist |
| Objection | PDPL (secondary) | Partly | Same as restriction; `opt_out` through a person | The patient cannot object to being on the programme through the system; objection to automated ordering is not offered (the score only orders work for staff) |
| Not to be subject to solely automated decisions | PDPL (secondary), GDPR Art. 22 practice | **Met by design** | No automated closure, no automated clinical decision, no message without approval or a signed template policy; closure needs a human signature (T-A1, T-A4, T-D1, T-D2, T-A74) | The unsigned outreach policy (H1) must stay unsigned-means-silent until a clinical lead signs (T-A72, T-A73) |
| Portability | PDPL (secondary) | Not built | none | Not assessed |
| Complaint | PDPL | Not applicable to the software | | Provider's channel and the UAE Data Office |

How requests reach the system. A request comes to the provider (controller). The provider's supervisor files the erasure in the system. There is no channel for a patient to file one directly. This is deliberate for the pilot (a patient's identity must be established by the provider) and is recorded as a gap.

## 9. Risks to individuals

Likelihood and severity are the author's judgement on a three-point scale (L, M, H), assuming a pilot on real data with the controls listed. They are not measurements. "Hazard" links to the same risk in [hazard-log.md](hazard-log.md), where the full control and test text is.

| ID | Risk to the individual | Likelihood | Severity | Mitigations built | Tests | Hazard |
|---|---|---|---|---|---|---|
| DR-01 | Data is processed without a settled lawful basis, or under the wrong regime (PDPL against health laws), so the person's rights are not those the provider promised | M | H | Consent ledger per channel and sharing; purpose limited by design; synthetic data only today | T-A23, T-A24 | HZ-05 |
| DR-02 | Erased data comes back from a backup or replica, or survives in a place outside the inventory | M | H | Erasure of 17 data places with verified 404 in HAPI and a zero-hit scan of every table and read route | T-A79 | HZ-34 |
| DR-03 | Erasure is refused when the person has a right to it, or granted when the retention duty forbids it; the clinical copy is erased too early or kept too long | M | H | Two-supervisor control with a fixed reason list; only closed cases; warning not to run on real data; no retention check (open) | T-A79 | HZ-34, HZ-41 |
| DR-04 | An unauthorised person reads or acts on a patient's case (wrong role, another clinician's patient, another patient, a stale session) | M | H | Default-deny routes by role, owner and patient scoping, token verification (iss, aud, exp, nbf, kid), 5-minute access tokens, least-privilege database role, production fails closed | T-F9, T-A76, T-A60, RT-Z cases in T-D21 | HZ-38, HZ-33 |
| DR-05 | A message reaches the wrong person, or discloses a screening result to a household member | M | H | No outbound text states or hints at the result (E14); owner approval of every draft; wrong-number flag; only pre-approved templates sent automatically | T-D1, T-D8, T-D13, T-A43 | HZ-02, HZ-09, HZ-21 |
| DR-06 | Personal data goes to an unapproved or out-of-country processor (model API, cloud, support access) | M | H | Template provider default; network switch off; https-only adapters; prompts without name, contact or history; hosting order ADR-026; in-country requirement recorded | T-D4, T-D6, T-D7, T-A25 | **HZ-39** (new) |
| DR-07 | Personal data leaks through logs, URLs, error text, the event log or an observability tool | L | H | Opaque actors and strict payload schemas; reference in a request body, not a URL; marker scan of logs and URLs; adapter logs without identifiers | T-A36, T-F10, T-F11, T-A75 | HZ-13 |
| DR-08 | Free text from a patient (reply) or staff (note) holds more health information than the purpose needs and is kept without limit | M | M | Reply text stored outside the log, readable by coordinator and supervisor only, 600-character read limit, erasable and tombstoned on erasure; no retention limit (open) | T-D19, T-A36, T-A79 | **HZ-41** (new) |
| DR-09 | A patient's opt-out or withdrawal is not acted on, so contact continues against their wish | M | M | Opt-out always goes to a person; consent withdrawal freezes outreach; hold on automation; synthetic accuracy 61 of 61 opt-outs sent to a person; most important unmeasured risk | T-A23, T-F7, T-D17, T-D18, T-D20 | HZ-05, HZ-25 |
| DR-10 | The person cannot see, correct or move their data (incomplete access export, no rectification route, staff data not erasable) | H | M | Timeline route; corrections as new events; erasure for the patient | T-A28, T-A79 | **HZ-40** (new) |
| DR-11 | The post-erasure log still lets someone who knows when a case was followed up single the person out | L | M | Random token per case, map deleted, no free text, ids of expunged resources | T-A79 | HZ-34 |
| DR-12 | A model or the score treats groups unfairly or gives wrong output, harming access to follow-up | M | M | Score only orders a worklist and does not decide; language removed as a feature; fairness audit by language, channel and age; reply triage detects Arabizi and Gulf dialect at run time and sends them to a person unless an Arabizi reply clears its own threshold (the audit found 81.0% and 91.1% auto-handled precision under the global threshold; 100% with the rule, synthetic test split); shadow period before use | T-D9, T-D10, T-D20 | HZ-18, HZ-25 |
| DR-13 | A model draft or reply carries an instruction, a diagnosis or false reassurance to the patient or an operator | L | H | Output validator, template fallback, human approval, structural limits on every automated actor; 737 red-team cases, 10 known limitations | T-D1, T-D2, T-D3, T-D8, T-D21 | HZ-09, HZ-28, HZ-29, HZ-31 |
| DR-14 | The system fails silently and a patient is lost to follow-up, which is a harm of its own | M | H | Idle-case watchdog, scheduler heartbeat, leader election, chaos tests | T-A30, T-F5, T-A78 | HZ-01, HZ-03, HZ-04 |
| DR-15 | A staff member is profiled or tracked through the log (actor ids, timestamps) with no purpose beyond the audit | L | M | Opaque actor ids; names only in an editable `users` row | T-A36 | HZ-40 |
| DR-16 | A development switch (dev login, simulation clock, demo approval) is left on in production | L | H | The API refuses to start outside dev with any of these on or with default secrets | T-A76, T-A77 | HZ-38, HZ-33 |

## 10. Measures that apply to all risks

| Area | Measure | Reference |
|---|---|---|
| Governance | Hazard log, decision matrix, RBAC matrix, red-team suite, model checking of the closure engine | E5, E6, F7, D14, A27 ([verification.md](verification.md)) |
| Human gates | Closure needs a clinician's evidence and signature; messages need approval or a signed template policy; erasure needs two supervisors | T-A1, T-D1, T-A79 |
| Technical | Append-only log, least privilege, fail-closed production start, pinned images, secrets scan, no PHI in logs or URLs | ADR-004, ADR-029, T-F10, T-F11, T-F12 |
| Organisational (not built) | DPO, privacy notice, processing agreement, retention schedule, breach procedure, staff training, access review, incident process | Pilot items in [pilot-hardening.md](pilot-hardening.md) |
| Breach notification | Not designed. PDPL and ADHICS notification duties and timelines are unverified (ADHICS V2 reportedly requires a data breach form to DoH, secondary source). | adhics-controls.md IM rows |

## 11. Residual risk and conditions for real data

| Risk | Residual (prototype, synthetic) | Residual (pilot on real data, controls as built) | What would lower it |
|---|---|---|---|
| DR-01 lawful basis and regime | L (no real data) | **H** | Legal opinion on PDPL scope; provider's documented basis; privacy notice |
| DR-03 erasure against retention | L | **H** | Retention schedule; decision on which system is the record; written rule for refusing erasure |
| DR-06 unapproved or out-of-country processor | L | **H** until hosting and any model route are approved in writing | DoH written authorisation (CM 4.2 exception route); in-country hosting; model route off |
| DR-02 backups and copies | L | **H** | Backup retention shorter than the erasure SLA, or a replay procedure; not designed |
| DR-04 unauthorised access | L | M | UAE PASS integration, MFA policy, revocation, account provisioning and review |
| DR-09 missed opt-out | L | M | Shadow mode; coordinator reads all replies; real labelled data |
| DR-10 rights incomplete | L | M | Complete export; staff erasure path; patient request channel |
| DR-05, DR-07, DR-08, DR-12 to DR-16 | L | L to M | See hazard log residuals |

Conditions before any real patient data is processed (all open): (1) legal reading of PDPL and Federal Law No. 2 of 2019 from the instruments, with article numbers; (2) retention schedule and decision on erasure of the clinical copy; (3) written DoH position on hosting and on any model route; (4) backup handling that respects erasure; (5) DPO appointed and this document reviewed; (6) clinical lead sign-off of the pathway, the wording and the outreach policy (H1); (7) UAE PASS or the provider's IdP in place, with an MFA decision; (8) privacy notice and processing agreement.

If these are not closed, the supported position is: the system runs on synthetic data only.

## 12. Consultation and sign-off

| Role | Name | Decision | Date | Status |
|---|---|---|---|---|
| Data protection officer (provider) | not appointed | Accept residual risk / require changes | - | **Pending** |
| Clinical lead (H1 reviewer) | not assigned | Confirm the pathway, wording, and that the risks to patients are complete | - | **Pending** |
| Controller's executive sponsor | not assigned | Approve the processing | - | **Pending** |
| Security lead (provider) | not assigned | Confirm technical measures against ADHICS | - | **Pending** |
| Authors | Engineering and governance leads | Prepared the draft | 2026-10-25 | Draft |

Consultation with data subjects: not done. A pilot should include patient advocates or a patient panel in the review of the message wording and the notice. Consultation with the regulator: DoH on hosting and AI classification; UAE Data Office on PDPL scope (both to verify).

Review triggers: a change of hosting, a model route switched on, a new data source (Malaffi), a new purpose, a new data category, a breach, or a regulator's reading.

## 13. Open verification items

1. PDPL article numbers for the impact assessment, rights, security, transfer and DPO provisions, and whether health data is inside the PDPL (regulatory-mapping.md items 1 to 4).
2. Federal Law No. 2 of 2019 Articles 13 and 20 and Ministerial Decision 51 of 2021, from the instruments; the retention trigger date and any Executive Regulations.
3. Whether the closure system holds the health record or only a workflow copy.
4. ADHICS V2 (May 2024) text, in particular the cloud, privacy and incident controls, and whether a vendor of this kind is a "Healthcare Technology and Services Provider" (adhics-controls.md).
5. Whether an in-country hosted model endpoint acceptable to DoH exists, for drafting or planning.
6. Whether DoH's Responsible AI Standard risk protocol classifies the two AI uses as high risk.
7. Controller and processor roles in a real pilot agreement.

## Sources and access dates

Accessed 2026-10-25 unless stated. Primary documents are those listed in regulatory-mapping.md ("Sources and access dates", 2026-10-18); this DPIA adds:

- Latham and Watkins, new UAE law regulates healthcare data (Article 20 retention, Article 13 localisation, secondary): https://lw.com/thoughtLeadership/lw-new-uae-law-regulates-healthcare-data
- Mondaq, UAE issues new healthcare data law (secondary): https://mondaq.com/healthcare/794770/uae-issues-new-healthcare-data-law
- Global Compliance News, new health data law UAE (secondary): https://www.globalcompliancenews.com/2019/04/15/new-health-data-law-uae-20190402/
- Al Tamimi, the Federal Law regulating ICT in the UAE healthcare sector (secondary): https://tamimi.com/law-update-articles/the-federal-law-regulating-the-use-of-information-and-communication-technology-in-the-uae-healthcare-sector
- Clym, Federal Decree-Law No. 45 summary and Modulos PDPL framework page (secondary, Article 21 impact assessment, seen in search results only): https://www.clym.io/regulations/federal-decree-law-no-45-united-arab-emirates and https://docs.modulos.ai/frameworks/uae-pdpl/security-breaches-dpia-and-cross-border-transfers
- Reference model for structure: GDPR Article 35(7) (not applicable law; not fetched, used as a heading model from general knowledge).
