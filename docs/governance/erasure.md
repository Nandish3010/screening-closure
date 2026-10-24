# Erasure of a patient's personal data (A32, ADR-025)

How a patient's personal data is deleted while the append-only event log keeps the audit trail. The design rule is [ADR-025](../../project/DECISIONS.md): the log holds opaque ids only; personal data lives in erasable places. This page is the inventory of those places, the procedure, what remains afterwards and why it identifies no one, and what is still open. It is a design and engineering description, not legal advice and not a compliance claim; the legal items are marked **to verify**.

Code: `api/app/erasure.py` (procedure), `api/app/routers/erasure.py` (routes), `api/migrations/016_erasure.sql` (register, transient map, the one scoped function). Test: `api/tests/test_erasure.py` (T-A79, `make test-db`).

## 1. Where personal data can live

| # | Place | What personal data it can hold | Erased by erasure? | How |
|---|---|---|---|---|
| 1 | HAPI: `Patient` | Name, identifiers (MRN), phone and address, birth date | Yes | `DELETE` then `$expunge` (section 3), then verified unreadable |
| 2 | HAPI: resources in the Patient compartment, e.g. `DiagnosticReport`, `Procedure`, `Observation`, `Encounter` | Subject reference, findings, free-text notes | Yes | Found with `Patient/{id}/$everything` (all pages), deleted and expunged |
| 3 | HAPI: resources the projection wrote for the cases: `Task`, `ServiceRequest`, `Appointment`, `Communication`, `Consent`, `Provenance` | Subject reference, or (when the Patient did not exist) the reference as an identifier and `display` | Yes | Ids computed from the case's events (`case_resource_ids`), deleted and expunged, referrers first |
| 4 | HAPI: `Practitioner`, `Device` (agents of Provenance and Task) | Staff names | No (staff are out of scope unless requested) | Left in place; see section 5 |
| 5 | HAPI history (`_history`, previous versions) | Every earlier version of the above | Yes | `expungePreviousVersions` |
| 6 | `events.payload` | The genesis event's `patient_ref` (`Patient/<id>`); `note` on human events (the one free-text field, up to a short length) | Yes | Reference replaced by an opaque token, notes replaced by a tombstone (section 3) |
| 7 | `drafts.text`, `drafts.rejection_reason` | The rendered message (names the owner, says a test found blood), a rejection note | Yes | Tombstone; draft id, channel, purpose, `prompt_hash`, approvals stay |
| 8 | `prompts.prompt_text` | The prompt sent to the drafting model | Yes (when no other case's draft uses the same prompt) | Tombstone; `prompt_hash` stays but can no longer be re-derived |
| 9 | `notifications.body` | The text "sent" to the patient | Yes | Tombstone; id, channel, status, `event_id` stay |
| 10 | `patient_replies.text` | What the patient wrote back | Yes | Tombstone; intent, confidence, model id, `needs_human` stay |
| 11 | `agency_tasks` (`input`, `output`, `resolution`) | The patient reference, where an agent's work pack carries it | Yes | Reference replaced by the token |
| 12 | `outbox.last_error`, `fhir_projection_queue.last_error`, `fhir_projection_dead_letter.last_error` | Error text that quotes the reference | Yes | Outbox: token; queue rows are deleted |
| 13 | HL7 contact registry (`app/hl7/contacts.py`) | A boolean per patient reference (never a number) | Yes | `contacts.forget()`; the registry is in process memory only, so a restart also clears it |
| 14 | `users` table | Staff display names | **No** | Out of scope, see section 5 |
| 15 | `erasures`, `erasure_map` | The register (opaque ids) and the transient map | Register: kept. Map: deleted at completion | Section 3 |
| 16 | Logs: API access log, service logs | The erasure request carries the reference in the body, not the URL (T-F11 allows only UUIDs in URLs); the access log holds the erasure id and the case ids of other routes, which are opaque. Earlier access-log lines of normal use hold case ids, never a reference | **No**: logs are not rewritten; they hold opaque ids only (T-F10, T-F11) | Open item 3 |
| 17 | Backups and replicas of Postgres and the HAPI database | Everything above, as at backup time | **No** | Section 5 and open item 2 |
| 18 | The browser | Whatever the web app cached | Not the server's to erase | Out of scope |

`tools/check/phi_scan.py` (T-F10, T-F11) already checks service logs and stored events for planted markers; erasure adds the scan of section 4.

## 2. The flow

Two supervisors; nothing is erased by a request.

1. `POST /erasures` with `{"patient_ref": "Patient/<id>", "reason": ...}` (supervisor). The reference is in the body, not the path as the ticket sketched (`/patients/{patient_ref}/erasure`): the security gate T-F11 (`phi_scan.py routes`) allows only UUIDs in URLs, and a URL is written to the access log, which erasure cannot scrub. `reason` is one of `data_subject_request`, `consent_withdrawn`, `collected_in_error`, `regulator_or_court_order`, `retention_period_ended`; no free text is accepted (422), so the register cannot collect personal data. The request is refused when any of the patient's cases is not closed (409: a clinician closes it first), when there is neither a case nor a FHIR Patient (404), and when an erasure of the same patient is already open (409). It writes the register row and the transient map and answers 202 with the erasure id.
2. `POST /erasures/{erasure_id}/approve` by a **different** supervisor (403 for the requester, for every other role and for every service token). Approving performs the erasure and answers with the register row.
3. `GET /erasures/{erasure_id}` (supervisor) reads the register row: status, reason, who requested and approved, number of cases, categories, counts. Never a reference, a name or text.

An alternative to the second supervisor, an approval token, is not built; the request/approve flow is the two-person control.

## 3. The procedure

In `approve`, holding a row lock on the register entry so only one approval runs at a time:

1. **HAPI first.** Delete then `POST [base]/{Type}/{id}/$expunge` (`expungeDeletedResources`, `expungePreviousVersions`) for the Patient, its compartment and the projected resources, referrers first and the Patient last, a few passes for resources HAPI refuses to delete while still referenced; then `GET` each and require 404. HAPI unreachable, refusing `$expunge` or leaving anything readable gives **502** and Postgres is untouched, so the request stays open and can be approved again.
2. The HL7 contact registry forgets the reference.
3. **Per case, one transaction with the event.** The store appends `erasure_completed` (actor `system:erasure`, payload `{erasure_id, categories}`) and, in the same transaction, calls `erasure_scrub_case(case, old_ref, token, tombstone)` and deletes the projection queue rows. The token is `Patient/erased-<32 hex>`, random, one per case (two cases of one patient are not linkable by it). The event id is derived from the erasure and the case, so a retry writes nothing twice.
4. **Finish.** The register row becomes `completed` (approver, time, categories, counts) and the `erasure_map` rows are **deleted**, in one transaction. After this nothing links a case to a patient: the map is gone and the log holds a random token.

A crash anywhere leaves a state a second `approve` completes: HAPI deletes are idempotent, cases already erased are skipped (and their categories read back from their own event), and the register is completed last. The test kills the process between steps 3 and 4 and retries.

**HAPI expunge.** HAPI soft-deletes on `DELETE` (history stays readable) and its expunge is off by default. `docker-compose.yml` sets `hapi.fhir.expunge_enabled` and `hapi.fhir.delete_expunge_enabled` to `true`; the CI HAPI container needs the same (`.github/workflows/check.yml`). Verified against HAPI 8.12.0 (the pinned image): after delete and `$expunge`, `GET Patient/{id}` and `Patient/{id}/_history` both answer 404. Without the flag `$expunge` is refused and the procedure stops with 502; it never reports a soft delete as an erasure.

**The append-only guards.** Migration 016 makes the guards on `events`, `notifications`, `prompts`, `drafts`, `outbox` and `agency_tasks` let an `UPDATE` through only when `current_user` is `closure_erasure`, a `NOLOGIN` role with no privilege except column-level `UPDATE` on exactly: `events.payload`, `drafts.text`, `drafts.rejection_reason`, `prompts.prompt_text`, `notifications.body`, `patient_replies.text`, `agency_tasks.input/output/resolution`, `outbox.last_error`. The only code running as that role is the `SECURITY DEFINER` function `erasure_scrub_case`, owned by it, with `EXECUTE` for the application role only. The function checks that the reference belongs to the case, that the token and tombstone have the expected shape, and touches only that case's rows. `DELETE` and `TRUNCATE` stay refused everywhere; the application role still has no `UPDATE` on the append-only tables; a session variable opens nothing (`current_user`, not a setting, decides). `test_ordinary_updates_and_deletes_are_still_refused`, `test_the_erasure_role_cannot_be_reached_from_outside` and `test_the_function_touches_one_case_of_one_patient_only` prove these. This does make the log "append-only except for the erasure function": a database superuser could always drop the triggers (limitation L30), and a bug in the function could now rewrite a payload of one case. The function is small and tested.

## 4. How it is proven

`test_t_a79_erasure_leaves_no_trace_of_the_patient_and_keeps_the_audit_trail` builds two patients (the one to erase has two closed cases; the other is a bystander) against a scratch database and a real HAPI, with a marker in every place of section 1 that a test can reach (HAPI Patient name, MRN, phone, address; DiagnosticReport and Procedure subject, conclusion and note; event notes; drafts incl. an approved one and a rejected one with a reason; a prompt; a notification; a patient reply; an agency task; an outbox row; projection queue and dead letter rows; the contact registry). Before the erasure it checks the scan finds hits in each (a scan that cannot find proves nothing). After:

- every row of every table of the database, as text, is searched for the markers and the patient id: **zero hits** (this includes `erasures` and `erasure_map`, and `users`);
- HAPI: `GET`, `_history`, searches by id, name, identifier, phone, address, `patient=` for ten resource types, the case identifier, and `$everything`: **zero**;
- every read route that can return something about the cases (`/cases`, `/cases/{id}`, `/timeline`, `/notifications`, `/fhir`, `/replies`, `/agency`, `/metrics/closure`, `/metrics/effort`): **zero**;
- the event log's shape is identical (same seq, type, time, actor, event id for every earlier event) plus exactly one `erasure_completed` per case, every case still replays to `closed`, `GET /metrics/closure` returns exactly what it returned before, the `close` event still names its evidence and signer, and the bystander's rows and HAPI resources are all still there.

## 5. What remains, and why it identifies no one

| Remains | Why it is kept | Why it does not identify a person |
|---|---|---|
| `cases` and `events`: case id, event ids, types, timestamps, actors (`role:<opaque id>`), SLA flags, evidence references (`DiagnosticReport/<id>`, `Procedure/<id>`, `Appointment/<id>`), signer (`Practitioner/<id>`) | This is the audit trail: that a result was followed up, by whom (a role), within the SLA, and how it closed | The patient reference is a random token; the FHIR ids point at resources that are expunged; the genesis `patient_ref` was never a name or an MRN (ADR-025: system-assigned id) |
| `drafts`, `notifications`, `patient_replies` rows without text | Provenance of what was sent, approvals, classification, for the audit and for the metrics | Text is a tombstone `[erased <erasure id>]`; remaining columns are enums, ids, hashes and times |
| `prompts` rows (hash, system prompt) | The hash links a draft to its prompt | The prompt text is a tombstone; the system prompt is generic |
| `erasures` register | Evidence that an erasure was requested, by whom, why, when, and what categories | Holds supervisor actors, a reason code, case ids, category names and counts. No patient reference (the map holding it is deleted) |
| `users` (staff display names) and the staff actors in the log | Staff are data subjects too (ADR-025) but are out of scope for A32 | They are staff, not the patient; `users` holds the display name in one editable row per person (A14), but no staff-erasure path exists and none was requested |
| `Practitioner`/`Device` in HAPI | As above | Staff and software |

**Re-identification risk.** "Cannot identify anyone" is a design target (ADR-025 consequence). With few cases, exact timestamps and rare event sequences, someone who knew when a particular patient was followed up could single the case out of the log, and the case id plus erasure time is in the register. Whether what remains counts as anonymised is a legal question, **to verify**. Nothing here makes timestamps coarser.

## 6. Open items and limits

1. **Retention conflict (to verify).** Federal Law No. 2 of 2019 (ICT in health fields) is read, in secondary sources only, as requiring health information to be kept for a minimum period (the period is not stated here: neither the instrument nor the figure was read first-hand, regulatory-mapping.md item 2). If that applies to the clinical record, erasing the FHIR clinical resources before the period ends may be unlawful, and erasure could need to be refused or restricted for that period (ADR-025, "Retention comes first"). The code has no retention check: the reason `retention_period_ended` is only a label a supervisor selects, and nothing verifies it. The supervisors' approval is the control. **Do not run erasure on real patient data until this is settled.** Whether the PDPL (Federal Decree-Law 45 of 2021) governs health data at all is the other open legal question (regulatory-mapping.md item 1).
2. **Backups and replicas** are not covered: a restore from a backup older than an erasure resurrects the data. A pilot needs either a backup retention shorter than the erasure SLA or a replay of the register after every restore (the register lists the erasures; `erasure_map` is deleted, so a replay would need the reference re-supplied by a person). Not designed.
3. **Logs.** Logs are not rewritten. By T-F10 and T-F11 they hold opaque ids only and no patient reference (the erasure request carries it in the body), so there should be nothing to scrub; that rests on the scan, not on a proof. A reverse proxy or API gateway in front of a deployment that logs request bodies would change that.
4. **Open cases** cannot be erased (409). A patient who withdraws mid-pathway has to have the case closed by a clinician first; there is no "closed because erased" closure. An erased case accepts no further event (the state machine refuses it), but the reply route does not check, so a coordinator could still store reply text on an erased case (it would get `logged: false`); not fixed here (another ticket owns the route).
5. **A new case for an erased patient** is a new relationship: if a lab sends a new result with the same HAPI id it would be a case for a patient that no longer exists in HAPI; the projection writes identifier-only references. Not prevented.
6. **Free text in HAPI** that is not in the Patient compartment (an `Observation` that does not reference the patient) is not found.
7. **HAPI search indexes.** HAPI runs here with hibernate search off, so no full-text index exists; a deployment that turns it on needs it reindexed or purged after expunge (not verified).
8. Erasure is one-way: there is no un-erase.
