# HL7 v2 adapter (A28, M6, ADR-039)

**Status: built and tested against synthetic messages only. It is not connected to Malaffi, has not been tested with it, and its field mappings are our reading of the standard and of the message types DoH Circular 2026/10 names.** The segment-level specifications ("Malaffi Real-Time HL7 Message Specifications") are available from the operator on request and were not seen; everything under "What Malaffi must confirm" below is open. The plan this implements is [malaffi-adapter-plan.md](malaffi-adapter-plan.md): HL7 v2 at the boundary, FHIR R4 inside (ADR-008). The engine and the state machine know nothing about HL7.

## What is built

| Piece | Where | What it does |
|---|---|---|
| Parser and writer | `api/app/hl7/parser.py` | Segments, fields, repetitions, components, sub-components, escape sequences (`\F\ \S\ \T\ \R\ \E\ \.br\ \Xhh\`), custom delimiters from MSH, `\r` / `\n` / `\r\n` separators, MLLP framing, timestamps with offsets, ACK writer. Standard library only. Errors carry an HL70357 code and a fixed phrase, never message content. Limits: 1 MiB, 2,000 segments. |
| Mappings and code sets | `api/app/hl7/handler.py`, `config.py` | See the tables below. Code sets and the identifier system are environment variables (`HL7_*`). |
| Contact validity | `api/app/hl7/contacts.py` | A boolean per patient, from ADT. No number is kept. |
| HTTP transport | `api/app/routers/hl7.py` | `POST /hl7`, the message as the text body, the ACK as the answer (`application/hl7-v2`), status 200 for every message that reached the handler. Only the `system:hl7` service token passes (`HL7_SERVICE_TOKEN`). |
| MLLP listener (optional) | `api/app/hl7/mllp.py`, compose service `hl7-listener` | Frames MLLP on port 2575 and posts each message to `POST /hl7` with the same token, so the handler is one. Off by default (compose profile `hl7`): `docker compose --profile hl7 up -d hl7-listener`. Loopback only, no database credentials. If the API cannot be reached the sender gets an AE, never silence. |
| Tools | `tools/hl7/` | `samples/` (one synthetic message per type, patients from the seeded cohort), `send.py` (HTTP or MLLP), `demo.py`, `make_samples.py`. `make hl7-demo` sends a positive FIT ORU and shows the case appear. |

### Authority

The adapter is the actor `system:hl7`. It has four actions (`policy.HL7_ACTIONS`, default deny) and two events (its row of the actor table, `ACTOR_EVENTS["system:hl7"] = {book_confirmatory, appointment_cancelled}`, A34, ADR-042):

1. **create a case** through the function behind `POST /cases` (`routers.cases.create_case`): same payload validation, same genesis event (actor `system:intake`), same automation hook. Not a second code path.
2. **book the confirmatory appointment**: the event `book_confirmatory` with `appointment_date`, `appointment_at` and `appointment_ref`, from `patient_acknowledged` only (the state machine decides).
3. **cancel the confirmatory appointment** (`cancel_appointment`): the event `appointment_cancelled` with reason `other` and `by_patient: false` (the SIU message does not say who cancelled or why, so nothing is guessed), from `confirmatory_booked` only; the case returns to `patient_acknowledged` and a later booking rebooks it. The milestone clocks do not move (A25).
4. **record confirmatory evidence** through `confirmatory.recorder`, the path of `POST /cases/{id}/confirmatory-result`. It appends no event.

The token reaches `POST /hl7` and no other route (`ROUTE_POLICY`; `docs/governance/rbac-matrix.md`, column `hl7`), and no event type through `POST /cases/{id}/events`. It cannot close, sign, reassign, accept ownership, change consent or confirm contact details; no automated actor can close a case. **A result received through the exchange never closes a case** (ADR-001): a recorded colonoscopy is evidence that a clinician may cite in a signed `close`, and `close` still checks it for completeness (ADR-036).

Outside `APP_ENV=dev` the published development token does not authenticate and needs no configuration to stay off; the adapter is on only when `HL7_SERVICE_TOKEN` holds a real value of at least 32 bytes.

## Message types and field mappings

All segment numbers are HL7 v2.5.1 as the standard defines them. Anything not listed is ignored.

### Common (every message)

| Field | Use |
|---|---|
| MSH-3, MSH-4, MSH-10 | Sender (application and facility) and control id: the idempotency key. A case id is a uuid5 over sender and MSH-10, a booking event id the same with a different prefix. |
| MSH-9 | Message type and trigger event. Others: ORU not R01, SIU not S12/S14/S15, ADT not A08/A31 are **AE** (`201`); any other message type is **AR** (`200`). |
| MSH-12 | Must start with `2.`; otherwise **AR** (`203`). MSH-11 (processing id), MSH-18 (character set) are not checked: UTF-8 is assumed. |
| PID-3 | The repetition whose assigning authority (CX-4, namespace id or universal id) equals `HL7_PATIENT_ID_SYSTEM` (default `SCREENING-CLOSURE`) gives `Patient/<id>`. The id must match `[A-Za-z0-9-]{1,64}`. No such identifier, or two different ones, is **AE**. There is no match on name, date of birth or sex: a fuzzy match is never made (hazard E6). |

### 1. ORU^R01, FIT result (case creation)

| Field | Use |
|---|---|
| OBX-3 | An observation whose code (component 1 or 4) is in `HL7_FIT_CODES` (default LOINC `29771-3` and the local code `FIT`) is the FIT result. |
| OBX-11 | `F` or `C` (final, corrected). Any other status is **AE**: a preliminary positive is never dropped silently and never acted on. |
| OBX-8 | The abnormal flag decides: `A AA H HH POS DET ABN` positive; `N NEG ND L LL` negative. |
| OBX-5 | Only when OBX-8 is empty: `positive pos detected abnormal reactive` / `negative neg not detected normal non-reactive`. A numeric value with no flag is **AE** (`result cannot be interpreted`): the adapter has no cut-off and does not invent one. |
| PID-13, PID-14 | A plausible telephone number sets `contact_valid` true on the case; otherwise the ADT registry is consulted; otherwise it is left absent. |

Positive: a case with `patient_ref = Patient/<id>`, `result_ref = DiagnosticReport/hl7-<case id>` (a reference; the adapter does not create that resource, see "Not built"), `contact_valid`. ACK **AA**. A redelivery of the same MSH-10 finds the case: **AA** with `duplicate message already processed`, nothing written. Negative: **AA**, `negative result acknowledged, no case created`, nothing written.

### 2. SIU^S12, S14, S15, the confirmatory appointment

| Field | Use |
|---|---|
| PID-3 | The patient, as above. Exactly one case of that patient in `patient_acknowledged` or `confirmatory_booked` must exist (S15: in `confirmatory_booked`); none or several is **AE** (never attached by guessing). |
| TQ1-7, else SCH-11 component 4, else AIS-4 | Appointment start. A timestamp with no offset is read in `HL7_UTC_OFFSET` (default `+0400`). Date only: `appointment_date`; date and time: also `appointment_at` (UTC). No readable start is **AE**. |
| SCH-2, else SCH-1 | `appointment_ref` if it fits the payload schema, otherwise dropped. |

- **S12** (book): `book_confirmatory` by `system:hl7` when the case is in `patient_acknowledged`. For a case already booked: the same time is **AA** (`appointment already booked for this time`); a different time is a reschedule (below).
- **S14** (reschedule): the same as S12. A case with no booking yet is booked. A booked case with a different time is cancelled (`appointment_cancelled`) and rebooked (`book_confirmatory`), the two events appended together (one transaction on the database store); **AA**. A redelivery changes nothing.
- **S15** (cancel): `appointment_cancelled` by `system:hl7` for the one case of the patient in `confirmatory_booked`; **AA**. A redelivery is **AA** `duplicate`. A patient whose case is in `patient_acknowledged` (nothing booked) is **AE** `no booked appointment to cancel for the case`; no case at all is **AE** `no matching case for the patient`.
- Other SIU triggers: **AE** `201`.

Redelivery of an S12 or S14 (same MSH-10) is **AA** `duplicate`, also after the case has moved on.

### 3. ORU^R01, colonoscopy report (evidence)

| Field | Use |
|---|---|
| OBR-4 | A code in `HL7_COLONOSCOPY_CODES` (LOINC `18746-8`, SNOMED `73761001`, CPT `45378 45380 45381 45384 45385`, local `COLONOSCOPY`) or `HL7_CT_COLONOGRAPHY_CODES` (`74261 74262 74263`, local `CTCOLON`) makes the message a colonoscopy report. |
| OBR-25 | `F` or `C`, else **AE**. |
| OBX with code `HL7_COMPLETENESS_CODE` (default `COLON-COMPLETENESS`), OBX-5 | `complete`, `incomplete` or `ct_colonography`, final (OBX-11 F or C). **Absent is AE (`colonoscopy completeness not stated`): the adapter never assumes `complete`**, because completeness is the one fact closure turns on (ADR-036). An unknown value is AE. A colonoscopy code cannot state `ct_colonography`; a CT-colonography code stating `complete` is recorded as `ct_colonography`. |
| OBR-7, else OBR-8 | The performed time, never later than the simulation clock. |

Exactly one case of the patient in `confirmatory_booked`, `confirmatory_completed`, `clinician_reviewed` or `reopened` (the states the existing route allows) must exist; none or several is **AE**. The recorder writes the `Procedure` and `DiagnosticReport` with deterministic ids, so redelivery changes nothing, and **an incomplete colonoscopy is recorded as incomplete** and cannot close a case. Recorder unavailable: **AE** (`evidence store unavailable, retry`).

### 4. ADT^A08, A31, contact validity only

PID-13 and PID-14 are judged: a telephone number with 7 to 15 digits is valid; an e-mail, fax or pager entry is not a number; no usable number is invalid; no telephone fields at all is **AA** with `no contact information in the message` and nothing recorded. The verdict (a boolean, keyed by `Patient/<id>`) goes into an in-memory registry and is applied as `contact_valid` to the next case created for that patient. The number is not stored, logged or returned. **An existing case is not changed**: `contact_updated` is a person's decision and is not in the adapter's row. Other ADT triggers are **AE**.

## ACKs

`MSA-1` AA, AE or AR; `MSA-2` the original MSH-10; `MSA-3` a fixed reason; for AE and AR an `ERR` segment with an HL70357 code (`100` segment sequence, `101` required field missing, `102` data type, `103` table value, `200/201/203` unsupported type, event, version, `204` unknown key, `205` duplicate key, `207` internal error) and the same reason. **AR**: unreadable (no MSH, bad delimiters, missing MSH-9 or MSH-10, too large, not UTF-8), unsupported message type, unsupported version. **AE**: readable but not applied, with the reason; the sender may correct and resend, and a transient failure (evidence store down, internal error) is an AE too. **AA**: applied, a duplicate, or a negative result deliberately not actioned. No reason ever contains message content.

## Logging and PHI

One line per message: message type, an internal kind, the ACK code, the fixed reason and a 12-hex-digit hash of sender and control id. No identifier, name, number, date or free text; an exception is logged by class only. `api/tests/test_hl7_logs.py` pushes marker values through every message type and error path and scans every log record and ACK. The case log holds codes and dates only (the payload schemas, ADR-019, apply). The genesis event of a case created from a message is `case_created` by `system:intake`, exactly as from `POST /cases`; the message's origin is not recorded on it (see "Not built").

## What Malaffi must confirm

Each item is an assumption in the code or the mapping that no document we hold settles. Added to the list in [malaffi-adapter-plan.md](malaffi-adapter-plan.md).

1. The real message profiles (segment, field and table level) for ORU, SIU and ADT, version (2.3, 2.4, 2.5.1), and the trigger events that carry each of our four facts. Everything above is a reading of the standard.
2. The patient identifier: whether the exchange sends the Emirates ID or a facility MRN, in which CX-4/CX-5, and how it is resolved to the identifier we hold (see "Not built": no Emirates-ID to Patient lookup exists).
3. How a positive FIT is flagged: LOINC `29771-3` or a local code, OBX-8 or OBX-5, OBX-11 values, and whether a numeric result with a cut-off is sent without a flag (we refuse it).
4. How a colonoscopy report states completeness, which procedure codes (CPT, SNOMED, local) it carries, whether a CT colonography is reported on the same path, and whether the report is a structured ORU or a document (MDM/ORU with an embedded report). The `COLON-COMPLETENESS` observation is our invention.
5. Appointments: whether the exchange sends SIU at all for a colonoscopy booked in a hospital system, which of S12, S14, S15 and S13 to S26 it uses, the time zone of timestamps without an offset, and whether a reschedule arrives as S14 or as S15 plus S12.
6. Contact data: which ADT triggers carry telephone numbers, which XTN components are populated, and whether the exchange applies the patient's consent or opt-out before sending (the plan's item 4).
7. Whether a non-EMR workflow system may receive these messages at all, directly or only through a connected provider's EMR (plan item 2), and the accession and certification steps before test traffic.
8. Acknowledgement behaviour: whether AA/AE/AR (original mode) is expected or enhanced mode (`CA/CE/CR`), the timeout, the retry policy of the sender, and whether duplicates are sent after an AE.
9. Transport: MLLP over what (VPN, TLS, a vendor gateway), the port, source IP allow-listing, and the certificate regime.
10. Message ordering and duplication: whether an A08 can arrive after the ORU it belongs to, and whether the same result can arrive from two sources with different control ids (it would then make two cases, as two `POST /cases` calls would).

## Not built

- **Outbound.** The only thing sent is an ACK. No status, ownership or due date goes to Malaffi or to a provider EMR (plan: "if the operator permits"; the outbox and the dead-letter queue the plan describes are not built), so there is no outbound failure visibility either.
- **Certificates, VPN, TLS, authentication of the sender.** `POST /hl7` is protected by a bearer token over whatever the deployment provides; MLLP is clear text and bound to the loopback interface in compose. No mutual TLS, no client certificates, no IP allow-list, no Malaffi security assessment material. A real link needs these from the operator.
- **Watchdog for the interface.** The plan's "silence on the interface is the dangerous failure" check (no message for a configured period) is not built.
- **Who or why a booking was cancelled.** S15 does not say, and the adapter does not read SCH-6 or the filler status for it: the event records reason `other`, not by the patient. A person can see the sender's reason in the sender's own log.
- **Contact updates on existing cases**, for the reason above; **a durable contact registry** (it is in memory and lost on a restart); merges and moves (A18, A40), which are refused as unsupported.
- **Identity matching beyond the exact identifier**: no Emirates-ID lookup, no name and date-of-birth match, no human queue for ambiguous matches (an ambiguous or missing match is an AE and the sender, or a person reading the sender's log, resolves it). No Arabic or transliteration handling.
- **FHIR mapping of the observation.** The adapter does not create the `Observation` and `DiagnosticReport` of the FIT result; the case's `result_ref` is a reference to a report that does not exist in HAPI (the projection writes it as an identifier-only reference, as it does for any absent resource). `registered_provider` is not mapped from the message (the API's default applies).
- **Provenance of origin.** The genesis event does not say a case came from HL7 (the payload schema has no field for it, and changing it was out of scope). The ACK, the log line (kind `fit_positive`) and the derived case id are the only traces.
- **Duplicate positives from different control ids** are two cases, as two calls to `POST /cases` are.
- **Scale.** Finding a patient's case reads the open cases of the relevant states (`load_cases`), one pass per message. Fine for a pilot cohort; an index on `patient_ref` is needed before volume.
- **Batch (FHS/BHS), ORM/RDS/RAS/VXU/PPR/OMP** and the other types in the circular's minimum dataset; enhanced-mode acknowledgements.
- **Sender allow-list and replay protection beyond MSH-10.**
