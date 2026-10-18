# Clinician review of the colorectal FIT pathway (AI-simulated)

> **AI-simulated clinical review (2026-10-18). Not a human clinical review. Human review H1 pending.**
>
> Written by a language model (Claude Opus 5.5) taking two roles at once: a consultant gastroenterologist who runs an endoscopy service, and a public-health physician who leads a population colorectal screening programme in the Gulf. No licensed clinician wrote, read or approved this document. It does not replace H1, it is not clinical advice, and it does not count as clinician approval of any template, string, SLA or rule. In particular it is **not** the "clinician pre-approval" that `docs/governance/ai-boundary.md` and the README name as the condition for automatically sending deterministic templates. All data in the project is synthetic.

Reviewed against: `docs/review/clinician-pack.md` (pathway `colorectal_fit` v0.2, template set v2, forbidden-phrase list v1), `docs/evidence.md` section 8, `config/pathways/colorectal_fit.yaml`, `api/app/ai/templates/en.yaml`, `web/src/i18n/strings.ts` (English), `docs/governance/ai-boundary.md`, and for the closure rule `api/app/payloads.py` and `api/app/evidence.py`.

## Source key

Each claim cites a source. The check column says how well this review checked it.

- **P**: read from the primary source by the project (`docs/evidence.md` marks it P). Not re-read here.
- **W**: checked by a web search during this review. Often a secondary source quoting the guideline; the original text was not opened.
- **M**: from the reviewer model's own knowledge. Not checked in this session. A human must check it before relying on it.

| Key | Source | Check |
|---|---|---|
| IFHAS | DoH Abu Dhabi, IFHAS standard V10, July 2025, sections 4.7.1, 4.9, 4.11, App. 9 (via `docs/evidence.md` 8b). https://www.doh.gov.ae/-/media/EE758C3B77314BC394F2479AEF803AA2.ashx | P |
| UAE-NG | UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed., 2022: clause 12.5.2, Appendices 2 and 3. https://www.isahd.ae/content/docs/Colorectal%20guideline%20Screening%20and%20Diagnosis_UAE%20National%20Guidelines.pdf | P |
| ALM | Almansoori et al., BMC Cancer 2021;21:897: 1,429 positives, 436 colonoscopies (30.5%), **49 of them incomplete**, median 72 days. https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ | P |
| SHIELD | DoH Circular 2025/42 appendix: positive blood test, GI consultation within 7 days, colonoscopy within 30 days ("to be validated") | P |
| CORLEY | Corley et al., JAMA 2017;317:1631: excess CRC risk appears at 10-12 months after a positive FIT | P |
| SR-9M | Systematic review, Clin Gastroenterol Hepatol 2021 (PMID 33010414): colonoscopy "should not be delayed beyond 9 months" | P |
| EU-QA | European guidelines for quality assurance in colorectal cancer screening and diagnosis, 1st ed., 2010. Source of the "31 days" interval and the 90% acceptable / 95% desirable pattern the UAE KPI table follows. Seen secondhand in the national evaluation by Santé publique France: https://www.santepubliquefrance.fr/en/cancers/colorectal-cancer/evaluation-colorectal-cancer-screening-program-2012-2013-period-national-indicators | W (secondary) |
| ESGE-CTC | Spada et al., ESGE/ESGAR guideline update 2020, Endoscopy, doi:10.1055/a-1258-4819. **Strong recommendation, moderate-quality evidence:** CT colonography (CTC) after a positive FIT when colonoscopy is incomplete or not feasible, in organised screening. CTC after an incomplete colonoscopy, preferably the same or the next day. | W |
| NHS-BCSP | NHS Bowel Cancer Screening Programme, local trust description: specialist screening practitioner (SSP) clinic within 14 days of a positive FIT, colonoscopy within 14 days of that clinic. https://www.uhsussex.nhs.uk/services/bowel-screening/ (the national standard itself was not opened) | W (local) |
| RCPATH | Royal College of Pathologists KPI proposals (July 2013): histopathology reported in 80% of cases within 7 calendar days and 90% within 10 calendar days of the procedure. Quoted by https://www.salisbury.nhs.uk/wards-departments/departments/pathology/cellular-pathology-user-manuals/turnaround-times-user-manual | W (secondary) |
| USMSTF-FIT | Robertson et al., USMSTF consensus on FIT, Gastroenterology 2017;152:1217: follow a positive FIT with colonoscopy, not a repeat FIT. A 2022 USMSTF time limit for this follow-up was searched for and not found. | M (W for "no fixed interval found") |
| USMSTF-SURV | Gupta et al., USMSTF post-polypectomy surveillance update, 2020 (GIE/Gastroenterology) | W (title only) |
| BBPS | Lai et al., Boston Bowel Preparation Scale, GIE 2009: total ≥6 with every segment ≥2 counts as adequate | M |
| KPI-COLO | Rees et al., BSG/ACPGBI/PHE colonoscopy KPIs, Gut 2016, and Kaminski et al., ESGE quality measures 2017: caecal intubation ≥90% (minimum) and ≥95% (target); adequate bowel preparation ≥90% | M |
| ANTICOAG | Veitch et al., BSG/ESGE guideline on antiplatelet and anticoagulant therapy for endoscopy, 2021 | M |
| USPSTF | USPSTF 2021 colorectal cancer screening statement: an abnormal stool test needs follow-up colonoscopy for the screening benefit; it sets no interval | M |

---

## 1. Overall verdict

**The pathway is clinically plausible for a supervised pilot on synthetic data, and as the outline of a real pilot, but it is not ready for real patients.** It needs the changes below and the human reviews H1 (clinician) and H2 (Arabic).

What is sound:

- One named owner at every step.
- Closure needs evidence and a human signature.
- No automated actor can close, approve or change consent.
- No result is given in a text message.
- The 15 and 31 working-day clocks are anchored in IFHAS and UAE-NG.

These target the documented failure: 30.5% follow-up at a median of 72 days (ALM). A real programme would build the same things.

Five gaps would make real-world use unsafe or the KPIs misleading. Each needs a decision before real patients:

1. **Closure evidence is too weak.** A bare `Procedure/<id>` closes a case today. Nothing checks that the colonoscopy was complete, the bowel preparation was adequate, histology came back, or the result reached the patient. An incomplete colonoscopy (49 of 436, about 11%, in ALM) could be closed as done. (Section 2.5)
2. **The 31 working-day clock is lenient.** It starts at the *latest* referral acceptance and restarts on every new acceptance. A case bounced between providers can wait without limit while every clock stays green. It also does not match the source's "of referral". (Section 2.3)
3. **There is no informed-refusal outcome.** Refusers either stay open forever or get mixed in with "lost". `patient_declined` sits wrongly among the provider's rejection reasons. (Section 2.6)
4. **"In person or by phone" may conflict with IFHAS.** `docs/evidence.md` 8b records IFHAS as requiring the participant to be **physically present** for an abnormal result. Patient-facing text promises a phone option. Only DoH or a licensed local clinician can say whether a phone call meets the standard. (Section 2.2)
5. **There is no safety-netting and no pre-procedure assessment.** Nothing makes sure a patient waiting for colonoscopy is told what to do if symptoms appear. Nothing screens for anticoagulants, diabetes medicines or fitness before booking (ANTICOAG). This belongs in the care team's call script, not in automated text. The pathway should still record that it happened. (Section 3, X)

## 2. Answers to the ten questions, and the extra questions

### 2.1 Q1: 2 working days to acknowledge, 1 to accept

**Is 2 working days to acknowledge realistic?** Yes, comfortably, when results arrive by electronic feed (LIS to the system), as Malaffi ORU feeds would (`docs/evidence.md` 8c). In practice "acknowledge" is a check by the laboratory or a screening clerk that the result is logged and routed, and 2 working days is generous for that. Keep 2.

**Is 1 working day to accept ownership realistic?** Only under one condition. The case must be **assigned by default** to the ordering physician, who IFHAS already makes responsible for communicating the result (IFHAS 4.9). "Accept" then means confirming, not finding someone, and a named cover rule must apply when that physician is on leave. Without default assignment, 1 working day will often breach and teach staff to ignore the flag. Two more points:

- The calendar counts Friday as a full working day, but government clinics close at 12:00 on Friday (`docs/evidence.md` 8d). A 1-working-day SLA that starts on Thursday afternoon is effectively a few hours long.
- An unowned positive result is the single most dangerous state in the pathway, because that is how results get lost. Keep the SLA short, and make its breach climb faster (Q9).

Recommendation: keep 1 working day **with** default assignment and cover. Otherwise use 2. Who does each step in practice: acknowledge is done by the laboratory system or the screening clerk; accept is done by the ordering primary-care physician, or by the screening nurse or coordinator on behalf of the clinic.

### 2.2 Q3: Non-disclosure, and should patients be told by text at all?

**Should patients be told by text at all?** Yes, but only as a prompt to engage, never as the result. Non-follow-up is the dominant harm in this population (ALM), and a neutral SMS or WhatsApp reaches far more people than a letter. Conditions:

- **Sender.** Text only from the facility's registered sender ID. UAE patients are wary of SMS scams, so a personal name on an unknown number reads like phishing. A `{clinic_name}` placeholder would help (change C4, not applied: it needs a new placeholder).
- **Neutral wording.** Text must stay neutral because phones are often shared within families in the Gulf. The generic "your screening result" protects privacy. Keep it generic in SMS and WhatsApp.
- **Same wording for normal results.** If the programme sends normal results by a different channel or with different wording, then "your care team needs to talk with you" tells the patient, by contrast, that the result is abnormal. Programme design question: send normal results with the same "result ready" wording as well (H-7).

**Is the first message acceptable?** Yes. "Your screening result is ready and your care team needs to talk with you about the next step" is honest, says nothing false, and does not reassure. Any patient will guess that something needs following up, and that is acceptable. A wording that hid the need for action would be worse.

**Is naming the next step ("a camera test", "colonoscopy") acceptable?** Yes, in my view. IFHAS concerns the result, not the procedure. Patients need the name to prepare and to ask questions, and the validator *requires* `{next_step}` in every message. A strict reading could argue the procedure name reveals the finding; that is for H1 to settle (H-2).

**Is "in person or by phone" right?** This has to be settled locally. `docs/evidence.md` 8b records IFHAS as "abnormal results require the participant physically present". A phone call is not physical presence. The NHS programme does use telephone SSP assessment, but that is not the UAE standard. Recommendation: if DoH reads IFHAS literally, change W08, T02 and T03 to "in person" (change C3, not applied). If a phone call is accepted, the call script must verify identity with two identifiers before the result is given. Record which reading was chosen, and by whom (H-1).

**When a patient replies asking for the result by text:** never disclose by text. A human calls within 1 working day. The automatic reply is the callback acknowledgement ("A person from your care team will call you to explain"), and the reply is logged as a `callback_requested`. Inbound replies need their own response SLA, which does not exist today.

**Is anything in section 9 alarming, wrong or missing?** Six things:

- **W05, the start page,** says "Open the link your care team sent you **to see your result**". The page never shows the result, so this sets up an expectation the page breaks (changed, C1). Separately, the validator rejects every link in a message (`url` rule), so no message can carry that link. How the patient gets the link is undefined (X).
- **W17** promises "easy steps to get ready". Bowel preparation is not easy for many patients, so the promise is mild false reassurance (changed, C2).
- **W20** says "A care coordinator will be chosen for you". The owner may be a doctor (changed, C5).
- **The stop-contact text (W42, W43)** does not say the patient can still reach the team (changed, C6).
- **Escalation notices (T13 to T15)** tell the patient about an internal service failure. They add worry without giving the patient anything to do. Do not send them by default (C7, not applied).
- **Safety-netting is missing.** This is the main omission (C8, not applied: it is clinical content).

### 2.3 Q4: What the 15 and 31 working days are measured from. Is 31 working days from acceptance acceptable?

**15 working days (IFHAS).**

- **Stop:** at the referral **request** (sent). IFHAS's "referral to secondary care within 15 working days of releasing results" is met when the referral leaves; the receiver's acceptance is a different measure. Keep.
- **Start:** at the result's **release**, meaning the laboratory authorisation time (`DiagnosticReport.issued`), not the moment the case was created in this system. Any ingestion lag otherwise disappears from the KPI. Change, for when real feeds exist.
- **Which referrals count:** only a referral to an endoscopy or GI service. A transfer between two primary-care owners is a change of owner, not a referral to secondary care, and must not stop the clock. Change.

**31 working days (UAE-NG Appendix 2, the EU-QA pattern). Is starting at acceptance acceptable?** Not as designed. As an operational per-case alarm, starting at acceptance would be tolerable only if two things held: the gap from request to acceptance is bounded (it is: 3 working days, then a bounce), **and** the clock never restarts. It restarts today on every later acceptance (ADR-028, "latest referral acceptance"). Each bounce-and-rebook hands the case a fresh 31 days, so the patient's actual wait is unbounded while no milestone ever breaches. The source says "within 31 working days **of referral**", and the EU-QA lineage measures from the positive test or referral, not from a provider's acceptance.

Recommendation:

1. Start the 31-working-day clock at the **first** referral request to an endoscopy service, and never restart it.
2. Report the per-case alarm as well as the programme share (above 90% acceptable, above 95% desirable), as the guideline does.
3. Also report **days from positive FIT to colonoscopy**, the measure the harm literature uses (CORLEY; SR-9M). For comparison, NHS-BCSP's pattern is about 28 days in total, and SHIELD's is 30 days for a positive blood test.
4. Settle the guideline's internal inconsistency (15 working days in 12.5.2, 31 working days in Appendix 2, 31 days in Appendix 3) with DoH (H-3).

**Is the 49-working-day worst case acceptable?** As a ceiling it is far inside the 9-month harm threshold (SR-9M). But it is about 10 calendar weeks, twice NHS-BCSP's pattern. With the clock fixed as above, the ceiling drops to about 15 + 31 = 46 working days, and the share KPI makes the typical case visible.

### 2.4 Q2: The other time limits

| Step | Verdict | Reason |
|---|---|---|
| contact_patient 3 wd | keep | Comfortably inside NHS-BCSP's 14-day SSP clinic. |
| patient_acknowledge 5 wd | change the response, not the number | The patient controls this step, so a breach must not climb the ladder as a staff failure. Make the breach trigger the **next contact attempt** (feeding the lost-contact rule, Q7), owned by the coordinator. The supervisor rung should come only after the lost-contact rule's attempts are used up. |
| book_confirmatory 5 wd | keep the number, add a check | Booking means a date is set, not that the test is done. **Add a check at booking: if the booked date falls after the 31-working-day milestone's due date, flag it at once** instead of waiting for the breach weeks later. Booking must include a pre-procedure assessment (anticoagulants, diabetes medicines, fitness, need for an interpreter) (ANTICOAG). |
| clinician_review 5 wd | keep for the endoscopy report | Histology takes 7 to 10 calendar days in 80 to 90% of cases (RCPATH), so review at 5 working days usually comes **before** the histology. That is fine for reviewing the endoscopy report. |
| close 3 wd | change the clock start | When tissue was taken, close should wait for the histology. Start the close clock at histology receipt, or add a pause for "awaiting histology". Otherwise most polypectomy cases will breach `close` for a reason no one can act on. |
| reassign 2 wd | keep | |

**Config:** no SLA value is changed in `config/pathways/colorectal_fit.yaml`. The issues above are about clock *start* and structure, not values. A value change without those structural fixes would hide the problem rather than solve it.

### 2.5 Q5: What counts as evidence to close a case

**What the system accepts today.** `EvidenceRef` accepts `DiagnosticReport/<id>` **or** `Procedure/<id>` (`api/app/payloads.py`). When `FHIR_VERIFY_EVIDENCE=true`, `api/app/evidence.py` checks only that the resource exists and belongs to the case. It checks no status, completeness, preparation quality or histology. With verification off, the default verifier accepts anything.

**Procedure or DiagnosticReport?** A `Procedure` records that something was done. It is not evidence that the screening question was answered. Closure should need:

1. **An endoscopy `DiagnosticReport` with status `final`.** It must record:
   - caecal intubation, with photo-documented landmarks;
   - bowel preparation quality, adequate meaning a BBPS total of at least 6 with every segment at least 2 (BBPS; KPI-COLO);
   - findings and polyps removed or retrieved;
   - the endoscopist's recommendation.
2. **A histopathology `DiagnosticReport`** whenever tissue was taken.
3. **A documented follow-up plan:** a surveillance interval (USMSTF-SURV, or the local equivalent), a return to routine screening, or a referral onward.
4. **A record that the outcome was given to the patient and to the primary-care physician.** A letter to the GP is required communication, but it is not evidence by itself.

A `Procedure` whose `status` is `stopped`, `not-done` or `entered-in-error` must **block** closure.

**An incomplete colonoscopy or inadequate preparation is not closable.** The case should go to a repeat colonoscopy, or to CTC, which ESGE-CTC strongly recommends after a positive FIT when colonoscopy is incomplete or not feasible, preferably the same or the next day after an incomplete one. This needs a loop back to booking, not a closure (Q10).

**Is CTC an alternative?** Yes, when colonoscopy is incomplete, contraindicated or declined after counselling (ESGE-CTC). A CTC report is acceptable evidence **if it is negative**. A CTC showing a lesion leads to a colonoscopy, not to closure. CTC is not a routine replacement for colonoscopy after a positive FIT, because it cannot remove polyps.

**When cancer is found,** the screening episode closes only when the onward referral (MDT or oncology) has been **accepted**. Closure means "screening question answered and handed on", not "care finished".

**Who may sign, and is clinician review alone ever enough?** A licensed physician signs: the endoscopist, or the responsible primary-care physician for the report. Never a coordinator. Clinician review alone is never enough. Signing means "I have read the evidence, the follow-up plan is set, and the patient has been told".

### 2.6 Q6: Informed refusal. Design proposal for a `declined` closure path (not implemented)

**What happens today:** nothing fits. `patient_declined` is a reason *a receiving provider* gives for rejecting a referral. That is the wrong actor: the patient declines, not the provider. A refuser therefore either stays open, breaching and climbing the ladder for ever, or gets marked lost, which is false.

**Proposal.** Add a refusal path that ends in closure with outcome `declined`. It needs a human sign-off and documented counselling.

- **Entry:** a new human-only event `record_informed_refusal`. It is allowed from `patient_contacted`, `patient_acknowledged`, `confirmatory_booked` and `referral_rejected`, and in the future from the incomplete-colonoscopy loop. It moves the case to a new side state `refusal_recorded`.
- **Required fields on the event:**
  - `counselled_by`: a licensed clinician, not a coordinator;
  - `counselling_ref`: a reference to the documented conversation, which must cover what a positive FIT means, the risks of not investigating, and the alternatives offered (CTC; a repeat FIT is **not** an alternative after a positive FIT, USMSTF-FIT);
  - `interpreter_used`: yes or no, and which language;
  - `capacity_confirmed`;
  - `date`.
- **Exit:** `close` with `outcome: declined`. It needs `signed_by` from a physician other than the counsellor or, at minimum, an explicit second confirmation, plus `evidence_ref` pointing to the counselling record. No automated actor can do this, as with every closure.
- **After closure:**
  - the primary-care physician is notified;
  - one offer to discuss again at 3 months, if the patient agreed to it;
  - the patient is told how to come back at any time;
  - no automated reminders.
- **Reopening:** any new contact from the patient, or a symptomatic presentation, reopens the case.
- **Metrics:** report `declined` closures **separately**. Counted as closed loops, they would inflate the programme's follow-up KPI (UAE-NG's 90% referral-to-colonoscopy measure).
- **Handoff reasons:** remove `patient_declined` from the reasons a provider may give. If a patient tells the receiving unit they decline, the unit rejects with a neutral reason and the referrer records the refusal.

### 2.7 Q7: Lost contact after 3 failed attempts

Three is a reasonable *minimum* only if "attempt" is defined. Proposed definition:

- **Attempts:** at least 3, spread over at least 10 working days.
- **Channels:** at least 2 different channels.
- **Timing:** at different times of day, including at least one after working hours.
- **Letter:** at least one letter to the registered address.

Before marking a case lost:

- check Malaffi or the EMR for updated contact details;
- check whether the patient was investigated elsewhere;
- tell the ordering physician;
- try the emergency contact if one is recorded.

Lost contact is not an ending. The case should be retried automatically, as a task for a human, at 3 months, and should flag on any new encounter the patient has.

The engine must count attempts from the notification and call log, not from a number the client types in. This is already listed as limitation L14. Final values are for H1 (H-4).

### 2.8 Q8: Opt-out consent default

Contacting a patient about their own result is part of direct care, so opt-out is defensible for **phone and letter**, and for **SMS** with neutral content. **WhatsApp** is a third-party platform that is often read on shared devices. I would make it **opt-in**, subject to DoH and data-protection advice (H-5).

Sharing the result with the specialist who will do the colonoscopy is part of the referral, so implied consent with a clear opt-out is usual. Its legal basis is for the human reviewer.

The **bigger risk is the mismatch**. The patient page shows phone only and sharing unticked, while the engine treats everything as allowed, so a patient who sees "phone" does not expect a WhatsApp message. **The page must show the engine's effective consent.** Whichever default is chosen, the two must agree.

"Stop all contact" after a positive FIT is high-stakes. Respect it, but:

- it should create a clinician task, not just a coordinator task;
- the primary-care physician should be told;
- the page should make clear the patient can still reach the team (C6).

Stopping contact is not a refusal of colonoscopy. Do not treat it as one.

### 2.9 Q9: Escalation and handoff

- **Ladder offsets 0, 3, 7.** These are reasonable for most steps. Against a 31-working-day clock, 7 working days to the coordinator is acceptable.
- **Faster climbing.** Two delays should climb faster:
  - an `accept_ownership` breach (no owner): supervisor at 1 working day;
  - any breach of the `confirmatory_completed` milestone: straight to the coordinator rung, since by then the national standard is already missed.
- **Per-step offsets.** The ladder should allow offsets per step or per milestone.
- **The 3-working-day handoff response time.** Realistic for referral triage in an endoscopy unit. Keep.
- **Rejection reasons.** Add:
  - `needs_pre_assessment` or `unfit_for_procedure`;
  - `already_investigated`, for a recent colonoscopy elsewhere;
  - `duplicate_referral`;
  - `out_of_area`.
- **`patient_declined`.** It is not a rejection; see Q6.

### 2.10 Q10: Missing exits

| Situation | How often (Gulf setting) | Proposed handling |
|---|---|---|
| Incomplete colonoscopy or poor preparation | Common: about 11% in ALM | A loop, not an exit. A new `confirmatory_incomplete` returns the case to booking, for a repeat or CTC (ESGE-CTC). The 31-working-day clock keeps running from the first referral. Needs its own SLA (H-6). |
| Investigated elsewhere, for example in a private facility | Common with a mixed public and private sector | Exit `investigated_elsewhere`. Closes only with the external report (Malaffi or uploaded), under the same evidence rule. |
| Moved abroad or out of the emirate | Common among non-nationals; less so in an Emirati cohort | Exit `transferred_out`, with a handover letter and a physician sign-off. Not counted as a followed-up case. |
| Unfit for colonoscopy | Uncommon | A clinician decides: CTC, or no investigation. Closure records the decision and its reason. |
| Symptomatic patient | Uncommon but urgent | Exit to the symptomatic or urgent pathway. Closes when that pathway accepts. |
| Died | Rare | Exit `deceased`, with a physician sign-off. |
| Declined after counselling | Not rare | The Q6 path. |

**Reopening.** The owner physician, the endoscopist or the programme lead may reopen. The current reasons are sensible. Add `histology_changes_plan` and `evidence_inadequate_on_audit` (the latter is close to the existing `evidence_inadequate`).

## 3. Feedback table

| Item | What | Keep / change | Comment |
|---|---|---|---|
| P1 | Purpose and scope | keep | The problem statement is right and sourced (ALM). Add that the screening episode closes on hand-on, not at the end of care. |
| P2 | Pathway picture and closure rule | change | The rule is right in principle but too weak in implementation. Tighten what counts as evidence (section 2.5). |
| S:result_received | | keep | Assign by default to the ordering physician (IFHAS 4.9). |
| S:acknowledged | | keep | |
| S:owner_accepted | | keep | Owner must be a role with cover, not an individual who may be on leave. |
| S:patient_contacted | | change | "Contacted" must mean a two-way conversation took place, not a message sent. Count attempts from the log (L14). |
| S:patient_acknowledged | | keep | A coordinator recording a phone conversation should be the normal route, not the exception. |
| S:confirmatory_booked | | change | Add the pre-procedure assessment and the booked-date check against the 31-working-day milestone. |
| S:confirmatory_completed | | change | Record whether it was complete, with preparation quality. If incomplete, loop back (Q10). |
| S:clinician_reviewed | | change | Separate "endoscopy report reviewed" from "histology reviewed". |
| S:closed | | change | Add closure outcomes: `investigated`, `declined`, `investigated_elsewhere`, `transferred_out`, `deceased`, `symptomatic_pathway`. Report them separately. |
| S:referral_rejected | | keep | |
| S:consent_withdrawn | | change | Create a clinician task and notify the primary-care physician. Withdrawal of contact is not refusal of colonoscopy. |
| S:lost_contact | | change | Define an attempt (Q7). Retry as a human task at 3 months. Flag on any new encounter. |
| S:reopened | | keep | Add reasons (Q10). |
| SLA:acknowledge | 2 wd | keep | Realistic with electronic feeds. |
| SLA:accept_ownership | 1 wd | keep, with a condition | Only with default assignment and cover; otherwise 2. The Friday half day bites hardest here. |
| SLA:contact_patient | 3 wd | keep | |
| SLA:patient_acknowledge | 5 wd | change | Keep the number. A breach should trigger the next attempt, not a staff escalation. |
| SLA:book_confirmatory | 5 wd | keep | Add the booked-date check. |
| SLA:clinician_review | 5 wd | keep | For the endoscopy report only. |
| SLA:close | 3 wd | change | Start the clock at histology receipt when tissue was taken (RCPATH). |
| SLA:reassign | 2 wd | keep | |
| M:referral_requested | 15 wd from case created | change | Start at the laboratory release time. Count only referrals to an endoscopy or GI service. |
| M:confirmatory_completed | 31 wd from latest acceptance | change | Start at the first referral request and never restart. Also report the programme share and days from FIT to colonoscopy. |
| L:reminder | 0 wd | keep | |
| L:supervisor | 3 wd | change | 1 wd for an `accept_ownership` breach. Per-step offsets. |
| L:coordinator | 7 wd | change | Straight to this rung for a breach of the 31-working-day milestone. |
| H:capacity | | keep | |
| H:wrong_service | | keep | |
| H:missing_info | | keep | |
| H:patient_declined | | change | Remove. Use the informed-refusal path (Q6). |
| H:timeout | | keep | |
| R1 | 3-attempt lost-contact rule | change | Keep 3 as a minimum, with a definition (Q7). Derive the count from the log. Settled by H1. |
| R2 | Opt-out consent default | change | Opt-out for phone, letter and SMS; opt-in for WhatsApp (needs a decision). The page must show the effective consent. |
| — | Non-disclosure wording (E14) | keep, with one question | Correct and well enforced. Open question: "by phone" against IFHAS "physically present" (H-1). |
| — | Closure evidence rule | change | DiagnosticReport (final) required. Histology when tissue was taken. A Procedure alone, or one stopped or not done, cannot close. Incomplete cases are not closable. |
| — | Informed refusal as a closure outcome | change | Add a `declined` path with counselling and a physician sign-off (Q6). Design proposal only. |
| W:frame | W01 to W05 | change | W05 promised the result; changed (C1). How the link reaches the patient is undefined. |
| W:result | W06 to W08 | keep, with one question | W08 "or by phone" pending H-1. |
| W:progress | W09 to W13 | keep | |
| W:next | W14 to W17 | change | W17 "easy" changed (C2). |
| W:owner | W18 to W27 | change | W20 changed (C5). |
| W:actions | W28 to W33 | keep | |
| W:consent | W34 to W46 | change | W42 and W43 now say the patient can still call (C6). Page defaults must match the engine. |
| W:status | W47 to W49 | keep | |
| M:result_notification | T01 to T03 | keep, with one question | Add the facility name (C4). "By phone" pending H-1. |
| M:reminder | T04 to T09 | keep | "Your care owner is" is internal jargon (C9, not applied). |
| M:appointment_confirmation | T10 to T12 | keep | Preparation instructions must come from the endoscopy unit, by a human. |
| M:escalation_notice | T13 to T15 | change | Do not send by default (C7). |
| M:callback_ack | T16 to T18 | keep | Also use it as the reply when a patient asks for the result by text. |
| D | D01 to D03 | keep | Demo only. |
| F:diagnosis_statement | | keep | It blocks "You have an appointment on ...", a perfectly good sentence. Acceptable strictness. |
| F:disease_term | | change | Add `piles`, `haemorrhoids` and `hemorrhoids`, and `IBS`. "It is probably piles" is the classic false reassurance after a positive FIT. |
| F:positive_result_jargon | | keep | |
| F:false_reassurance | | change | Add risk statements ("small chance", "low risk", "1 in"), and promises about the procedure ("painless", "safe", "easy"). |
| F:clinical_judgement | | keep | It blocks "emergency", so safety-netting must come from templates or people, which is correct. |
| F:medication_or_treatment | | change | Add `biopsy` and `anticoagul\w*`/`blood thinner`. |
| F:advice | | keep | |
| F:result_disclosure | | change | Add `bleed\w*`/`bled` (for example "your test picked up bleeding" passes today). Keep "blood test" allowed, since it names a different test, but add a tested example to show it is not mistaken for disclosure. |
| Q1 | | answered | Section 2.1. |
| Q2 | | answered | Section 2.4. |
| Q3 | | answered | Section 2.2. |
| Q4 | | answered | Section 2.3. |
| Q5 | | answered | Section 2.5. |
| Q6 | | answered | Section 2.6. |
| Q7 | | answered | Section 2.7. |
| Q8 | | answered | Section 2.8. |
| Q9 | | answered | Section 2.9. |
| Q10 | | answered | Section 2.10. |
| X | Anything missing or dangerous | see the list below | |

**X: missing or dangerous, not shown by the pack**

1. **Claims of automatic sending.** README, `docs/design-choices.md`, `project/PLAN.md`, `docs/submission/technical-documentation.md` and `docs/governance/ai-boundary.md` describe "automatic sending of clinician-pre-approved deterministic templates". No clinician has approved any template (H1 is `todo`), and no automatic-send code was found under `api/app` or `config`. The wording should say "subject to H1" until a human signs. **This AI review is not that approval.**
2. **The default evidence verifier accepts anything** when `FHIR_VERIFY_EVIDENCE` is off. Acceptable for the demo; must be on for any pilot.
3. **No safety-netting record.** The pathway should record that the patient was told whom to call if symptoms appear before the test. The local emergency number and wording must come from a local clinician (C8).
4. **No pre-procedure assessment step** for anticoagulants, antiplatelets, diabetes medicines, iron, renal function before bowel preparation, sedation, an escort and an interpreter (ANTICOAG).
5. **FIT positivity threshold.** ALM reports 13.5% positivity, which is high. The threshold (µg Hb/g) drives colonoscopy demand and so whether the 31 working days is feasible at all. Endoscopy capacity is not modelled against it (H-8).
6. **Friday half day** is counted as a whole working day (`docs/evidence.md` 8d). This matters most for the 1-working-day SLA.
7. **Languages beyond Arabic and English.** If the cohort includes non-nationals, it may include Urdu, Hindi, Malayalam or Tagalog speakers. Interpreter use should be recorded at the conversation.
8. **Inbound replies have no SLA.** "Reply to book a time" invites replies, so a response time must be set and owned.
9. **Arabic and English now differ** for the five web strings changed below. H2 must update the Arabic to match.

## 4. Recommended wording changes (English)

Applied changes carry the comment `// AI clinical review 2026-10-18` in `web/src/i18n/strings.ts`. All are English-only. The matching Arabic strings were **not** changed and need H2.

| # | ID / key | Before | After | Applied? | Why |
|---|---|---|---|---|---|
| C1 | W05 `homeBody` | Open the link your care team sent you to see your result. You can add this page to your home screen. | Open the link your care team sent you to see your next steps. You can add this page to your home screen. | **yes** | The page never shows the result, so do not promise it. |
| C2 | W17 `nextSteps[2]` | You will get a date and easy steps to get ready. | You will get a date and clear steps to get ready. | **yes** | Bowel preparation is not easy for many people; "easy" is mild false reassurance. |
| C3 | W08 `explainInPerson`; T02, T03 | Your care team will explain your result in person or by phone. | Your care team will explain your result in person. | no | Depends on how IFHAS "physically present" is read (H-1). |
| C4 | T01 and every first contact | … {owner_name} will call you … | Message from {clinic_name}: … {owner_name} will call you … | no | Builds trust against scams. Needs a new placeholder, a validator change and Arabic. |
| C5 | W20 `ownerPending` | A care coordinator will be chosen for you soon. | A person in your care team will be chosen to look after you soon. | **yes** | The owner may be a doctor, not a coordinator. |
| C6 | W42 `withdrawHint` | If you stop contact, your care team will not message or call you. A person will check your case. | If you stop contact, your care team will not message or call you. A person will check your case. You can still call your care team. | **yes** | The patient must know the door stays open. |
| C6 | W43 `withdrawnNotice` | You asked us to stop contact. We will not message or call you. A person on your care team will check your case. | You asked us to stop contact. We will not message or call you. A person on your care team will check your case. You can still call your care team. | **yes** | Same. |
| C7 | T13 to T15 `escalation_notice` | Your screening result is ready. We want to make sure your next step is not delayed. A senior member of your care team has been told. … | Do not send by default. If kept: Your screening result is ready. We are sorry we have not spoken with you yet. {owner_name} will call you, or you can reply to book a time. The next step is {next_step}. | no | Telling a patient about internal escalation adds worry without giving them anything to do. A policy decision for H1. |
| C8 | new: page and call script | none | A safety-netting line, for example "If you feel very unwell before your appointment, call your doctor", with the local emergency number | no | Clinical content. The wording and the number must come from a licensed local clinician. |
| C9 | T05, T07, T09 to T12, T16 to T18 | Your care owner is {owner_name}. | Your care team contact is {owner_name}. | no | "Care owner" is internal jargon. Not applied because `api/tests/test_ai_templates.py` (lines 123 to 125) uses the old phrase to mutate the template, so the change needs a test update and the Arabic. |

No change was made to `api/app/ai/templates/en.yaml`. Every template change worth making either needs a clinician's decision (C3, C7, C8), a new placeholder (C4) or a test change (C9).

## 5. Questions only a licensed local clinician (or DoH) can settle

- **H-1.** Does a telephone conversation meet IFHAS's requirement that the participant be physically present when an abnormal result is given? This decides C3 and the call script.
- **H-2.** May an automated message name the next step ("a camera test", "colonoscopy") before the conversation?
- **H-3.** Which of the guideline's figures governs: 15 working days (clause 12.5.2), 31 working days (Appendix 2) or 31 days (Appendix 3)? And does "of referral" mean the request or the acceptance?
- **H-4.** What counts as a contact attempt, how many, how far apart, and must the emergency contact or a registered letter be used before a case is marked lost?
- **H-5.** May WhatsApp and SMS be used opt-out for this purpose under DoH rules and UAE data-protection law? What is the legal basis for sharing with the specialist?
- **H-6.** What is the local rule after an incomplete colonoscopy or inadequate preparation (repeat colonoscopy, same-day or next-day CTC), and the SLA for it? Is CTC available at the pilot sites?
- **H-7.** Are normal FIT results sent with the same "result ready" wording? If not, the positive-result message discloses by contrast.
- **H-8.** What is the programme's FIT positivity threshold, and does local endoscopy capacity support 31 working days for the resulting volume?
- **H-9.** Who may sign a closure (endoscopist, primary-care physician, programme lead), and who may sign a `declined` closure? Is a second signature required?
- **H-10.** What exact safety-netting wording and emergency number should patients receive, and through which channel?
- **H-11.** After informed refusal, is a re-offer at 3 months acceptable locally? What is the rule for patients 76 to 85 (UAE-NG: decided individually)?
- **H-12.** Sign-off of all patient-facing English wording, including the changes applied here, and of the forbidden-phrase list (H1). The Arabic goes to H2.
