# Patient app and privacy centre

The mobile patient experience at `/patient`, and the privacy and consent centre inside it. All data is synthetic. Decisions: ADR-052. The Arabic text has had no native-speaker review (H2); it is in [the Arabic review pack](../review/arabic-pack.md).

## What the patient gets

| Screen | What it shows | Source |
|---|---|---|
| My care, "My result" | One plain paragraph: the result is ready and the care team will explain it in person or by phone. It states no finding (E14). | `STRINGS.resultBody` |
| My care, "My next step" | What to do next, in the pathway's own words, with at most one primary button: confirm (`patient_acknowledge`), add the booked appointment to the calendar, or allow contact again after a withdrawal. "I don't understand, call me" is always there (`callback_requested`). | `lib/patientApp.ts` `nextStepOf` |
| My care, "My appointment" | The booked date (and time if there is one), the appointment named as its pathway names it, "Add to my calendar" (an `.ics` file made on the phone; nothing is uploaded), "Ask for another date" (`callback_requested`, reason `reschedule`) and "I cannot come" (reason `cannot_attend`). | `book_confirmatory` payload; `buildIcs` |
| My care, owner | The owner's name and role (`care_contact`), a call button to the care team's line (a labelled synthetic number). | `GET /cases/{id}` |
| My care, status | The journey strip and a plain-language list of the steps the patient is allowed to see, newest first. | the patient's allow-listed events |
| Privacy, choices | Channels (SMS, WhatsApp, phone, letter), sharing with the specialist, the app and message language, stop all contact (with a confirmation) and "you may contact me again". | `consent_updated` |
| Privacy, who looked at my record | Each staff member who opened the record: name, role and time. | `GET /cases/{id}/access-log` |
| Privacy, how we use your data | Synthetic data; software only prepares and sorts messages; a person decides everything about care; what the patient writes is kept apart from the care record and can be erased; the log holds steps, dates and roles; erasure after closure needs two supervisors. Each line is a fact the repository already enforces. | `STRINGS.data` |
| Privacy, this phone | What the phone keeps for offline use, and a button that removes it. | `localStorage` `sc.patient.last` |

Pathways: `colorectal_fit` (camera test, colonoscopy), `cervical_hpv` (a follow-up examination), `chronic_conditions` (a visit with the family doctor), anything else (an appointment). The cervical and chronic wording is the packs' `patient_messages.next_step` (placeholders, H1).

## Consent: the engine is unchanged

Every choice posts the existing `consent_updated` with only the booleans the patient changed. Withdrawing is `withdrawn: true`, restoring is `withdrawn: false`; the engine's consent hold (outreach frozen, a person asked to review, a pending referral held, ADR-044) applies as before. The app adds a confirmation step before a withdrawal; nothing is written until the patient confirms.

Two things the brief named have no engine event and were not added (ADR-052): a patient "I will attend" confirmation of a booked appointment, and a language preference stored without an action. The language chosen in the app is sent as `lang` with the buttons that reach the care team, which is what the outreach reads.

## Who looked at my record (access audit)

- **What is written.** A staff user's read of a case: the case page, its timeline, the messages sent, the FHIR ids, the stored patient replies. Table `access_audit` (migration 020): case, actor (`role:opaque_id`, as in the log), role, kind of read, simulation time. Append-only; the application role cannot UPDATE or DELETE.
- **What is not.** The patient's own reads; service tokens and in-process automation (scheduler, watchdog, agency, HL7 adapter, the automation scans), which the data panel describes; the worklist (`GET /cases` lists state and owner of many cases); reading the access log.
- **Bounded.** One row per (case, person, kind) per 15 minutes of simulation time. A page left open does not fill the table. The patient is shown the newest 100 rows, staff the newest 500.
- **Not in the event log.** A read changes no state. The state machine, replay and model checker do not see it.
- **Failure.** If the row cannot be written, the read still succeeds and a warning is logged (clinical availability first).
- **Staff UI.** The case page ends with "Who has looked at this record": when, who, role and what was read. The case's owner, coordinators and supervisors may read it; the lab may not; a clinician who is not the owner is refused (403), as for the other owner-scoped reads.

## What a patient is never sent

The patient's answer is an allow-list (SEC-2): no priority tier or rule, no risk score or factors, no model id or prompt hash, no staff notes or reasons, no escalation or SLA internals, no other patient, no staff id. The one staff name is the owner's, in `care_contact`. `api/tests/test_patient_view.py` and `api/tests/test_access_audit.py` (`test_the_patient_app_payloads_hold_no_staff_only_data`) prove it, and `tests/test_rbac_matrix.py` covers the new route for every role.

## Installable and offline

`manifest.webmanifest`: standalone, portrait, start `/patient`, a shortcut to `/patient?tab=privacy`, 192 and 512 icons. `sw.js` caches the app shell and hashed static files only: it does not touch API calls, other origins, non-GET requests, or any response marked `no-store` or `private`; pages are network first, hashed assets cache first. `web/tests/serviceWorker.test.ts` runs the worker against a fake network to prove it. Offline, the last case shown is read from `localStorage` (what the patient already saw); the buttons wait and say why. On a wide screen the app is drawn inside a phone frame (CSS only, so a judge or a video sees a phone).

## Accessibility

Buttons and rows are at least 48 px tall, the main buttons 60 px. Contrast uses the shared tokens in light and dark, checked by `web/e2e/a11y.spec.ts` (axe) for the home screen, the privacy centre and the withdrawal confirmation, in English and Arabic. Focus is visible; changing tab moves focus to the new heading; the bottom menu is a labelled `nav` with `aria-current`; every control has an accessible name. The layout uses logical properties (start/end), the language toggle names both languages in their own script, a Latin name inside Arabic text is isolated, and `dir`/`lang` follow the language.

## Limits

- The care-team phone number is a placeholder. Arabic and the cervical and chronic English are unreviewed (H1, H2).
- The access log shows what the system records from now on; reads before migration 020 are not in it.
- `Add to my calendar` is a file download: iOS and Android open it with their calendar; a desktop browser saves it.
