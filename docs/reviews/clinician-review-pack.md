# Clinician review pack: thresholds, priorities and timings

For: a doctor or nurse. Time: about 2 hours. Status: not yet sent to anyone.

This is a health-screening hackathon prototype (FHDIC Abu Dhabi Challenge 05, "Closing the Screening-to-Care Loop"; team "Humans Decide"). It tracks a patient from an abnormal screening result to a documented outcome, with one named owner at every step, time limits, escalation to a person, and closure only with evidence and a clinician's signature. All data is synthetic.

## 1. What we are asking

Check the clinical numbers the prototype currently uses. None of them has been reviewed by a clinician. Where the files say "placeholder" they are our own design values, or values carried over from another pathway, and must not be read as guidance.

Three pathway packs are in scope:

- Bowel screening, positive FIT: `config/pathways/colorectal_fit.yaml`
- Cervical screening, abnormal HPV or cytology: `config/pathways/cervical_hpv.yaml`
- Long-term conditions, abnormal blood pressure, HbA1c, glucose, LDL or BMI: `config/pathways/chronic_conditions.yaml`

For each row in Appendix A, mark **agree**, **change to ___**, or **comment**. "Cannot say" is a valid answer. A short answer is more useful than none.

**Time (about 2 hours).** 15 minutes to read this page. 75 minutes for Appendix A (35 rows). 20 minutes for the patient wording in the existing pack (optional, see section 4). 10 minutes for the sign-off lines in section 3.

**Start here if you are short of time.** Rows T9 to T12 (referral and procedure clocks), T17 (lost contact), P1 to P11 (priority tiers and cut-offs), D1 and D2 (what "complete" means for closure).

## 2. What you are not being asked to do

- No diagnosis, no advice, no judgement about any person. There are no patients.
- No patient data. Everything you will see is synthetic or a rule written in a file.
- No review of the software, the security or the data-protection design.
- No commitment to anything beyond this review. You are not endorsing the product, and nothing here is a clinical sign-off for real use.

## 3. How your sign-off is recorded

The repository already has a process for this (H1, the clinician review; `project/HANDOVER.md` section 2, tracker row H1 in `project/TRACKER.md`, test case T-E13):

1. You fill in the last column of Appendix A and the sign-off lines below, on paper, in a copy of this file, or by email. Nothing is entered on your behalf.
2. The owner records your answers as a dated review section in `docs/governance/clinician-review.md`, naming you only if you agree to be named. That file currently holds an AI-simulated review labelled "not a human clinical review"; your answers will be recorded separately and will not be mixed with it.
3. The owner changes the pathway files only for rows where you wrote "change to", re-runs the project checks, and sets tracker row H1 to done.
4. The "reviewed by" credit in the submission documents (technical documentation, section on H1 and H2) uses the name and wording you approve.

A separate, optional step, not requested by this pack: `config/outreach_policy.yaml` has `approved_by: null`, so automatic patient messages are off until a named clinical lead signs the whole message policy at template-set version 3. Reviewing this pack is not that signature. If you are in a position to sign it and want to, say so; otherwise it stays unsigned.

**Sign-off lines (fill in):**

- Reviewed by (name and role): ______________________  Date: __________
- I reviewed Appendix A on synthetic material only: yes / no
- You may name me in the submission as: ______________________  or  keep me anonymous: yes / no
- Rows I could not judge: ______________________

## 4. Other material, if you have time

- `docs/review/clinician-pack.md`: the generated 30-minute pack for the bowel pathway (picture of the states, ten questions, all 179 English patient strings). Its section 11 is the shorter alternative to this pack.
- `docs/governance/clinician-review.md`: an AI-simulated review, with 12 open questions for a licensed clinician (H-1 to H-12). Some rows below cite it. It is not evidence and is not a clinician's opinion.
- `docs/evidence.md` sections 8a to 8d: the sources the sourced numbers come from.

---

## Appendix A. Items to review

Key to the source column: **sourced** means the YAML names a document and clause; **assumed** means the YAML says the value is our own design value; **PLACEHOLDER** means the YAML itself uses that word and "needs clinician sign-off (H1)". "wd" is working days (Monday to Friday, Asia/Dubai, no public holidays configured, `config/calendar.yaml`). Line numbers refer to the files as of this branch.

### A1. Per-state time limits (working days allowed to perform the step that leaves the state)

The colorectal values are "our own design values (assumed, not sourced)" (`colorectal_fit.yaml` lines 111-112; `docs/assumptions-limitations.md` L12). The cervical and long-term-conditions files carry the same eight values over and mark each block "PLACEHOLDER, needs clinician sign-off (H1): every value in this block" (`cervical_hpv.yaml` lines 22-26; `chronic_conditions.yaml` lines 24-26). One row covers all three files.

| # | Item | Current value (colorectal_fit.yaml : cervical_hpv.yaml : chronic_conditions.yaml) | Source / rationale | Reviewer: agree / change to ___ / comment |
|---|---|---|---|---|
| T1 | Acknowledge the result | 2 wd (l.118 : l.26 : l.28) | Assumed. AI-simulated review: realistic with an electronic feed (`clinician-review.md` 2.1) | |
| T2 | Accept ownership | 1 wd (l.119 : l.27 : l.29) | Assumed. AI-simulated review: realistic only if the case is assigned by default to the ordering physician, with cover for leave (2.1) | |
| T3 | Contact the patient | 3 wd (l.120 : l.28 : l.30) | Assumed | |
| T4 | Patient acknowledges | 5 wd (l.121 : l.29 : l.31) | Assumed. The patient controls this step; the AI-simulated review suggested a breach should prompt the next contact attempt rather than blame staff (2.4) | |
| T5 | Book the confirmatory procedure (colonoscopy / colposcopy / care visit) | 5 wd (l.122 : l.30 : l.32). It covers the referral request, the receiving provider's acceptance and the booking | Assumed | |
| T6 | Clinician reviews the outcome | 5 wd (l.123 : l.31 : l.33) | Assumed | |
| T7 | Close (sign-off after review) | 3 wd (l.124 : l.32 : l.34) | Assumed; added so a reviewed case awaiting signature can go overdue (L12) | |
| T8 | Re-route a rejected or bounced referral | 2 wd (l.125 : l.33 : l.35) | Assumed | |

### A2. Clocks that span several steps (milestones)

| # | Item | Current value | Source / rationale | Reviewer |
|---|---|---|---|---|
| T9 | Referral requested after the result is released (all three pathways) | 15 wd from the result release to the first referral request (`colorectal_fit.yaml` l.143-147; `cervical_hpv.yaml` l.42-46; `chronic_conditions.yaml` l.44-48) | Sourced: DoH IFHAS standard V10, July 2025, "referral to secondary care within 15 working days of releasing results" (`docs/evidence.md` 8a/8b). Open point in the cervical and chronic files: does this clause govern a cervical result, or a referral to a family physician? | |
| T10 | Colonoscopy completed (bowel) | 31 wd from the first referral request; the clock never restarts (`colorectal_fit.yaml` l.148-152) | Sourced: UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed. 2022, Appendix 2 (KPI table). The guideline is inconsistent with itself: clause 12.5.2 says 15 working days for urgent referral, Appendix 3 says 31 days (`colorectal_fit.yaml` l.139-141). Does "of referral" mean the request or the acceptance? (`clinician-review.md` H-3) | |
| T11 | Colposcopy completed (cervical) | 31 wd from the first referral request (`cervical_hpv.yaml` l.47-51) | PLACEHOLDER: the colorectal colonoscopy figure carried over so the clock runs; "NOT a cervical standard"; no cervical timeline is in the repository | |
| T12 | Family-physician visit completed (long-term conditions) | 20 wd from the first referral request (`chronic_conditions.yaml` l.49-53) | PLACEHOLDER: "a design value; no primary-care follow-up standard is in the repository". The priority tier does not change this clock | |

### A3. Escalation and handoff timings

| # | Item | Current value | Source / rationale | Reviewer |
|---|---|---|---|---|
| T13 | Escalation rung 1: reminder to the case owner | 0 wd after the due date (`colorectal_fit.yaml` l.157). Accepted by the primary-care or specialist owner | Assumed (ADR-014). Same ladder in all three pathways (the cervical and chronic files inherit it) | |
| T14 | Escalation rung 2: supervisor told and asked to act | 3 wd after the due date (l.158) | Assumed | |
| T15 | Escalation rung 3: programme coordinator told and asked to act | 7 wd after the due date (l.159) | Assumed | |
| T16 | Referral request unanswered before it is bounced back | 3 wd (`handoff_response_working_days`, l.163) | Assumed (ADR-016) | |
| T17 | Lost contact: the least number of failed attempts before a case may be marked "lost contact" | 3 failed attempts (`colorectal_fit.yaml` l.45, `min: {failed_attempts: 3}`), inherited by the other two packs | Assumed, "pending clinician review" (ADR-011; L14). Not from a guideline. Open: what counts as an attempt, the gap between attempts, whether the emergency contact or a letter must be tried first (`clinician-review.md` H-4) | |
| T18 | Re-offer date after an informed refusal | 12 months after the refusal (`informed_refusal.re_offer_months`, l.174). Nothing re-offers automatically | Assumed 12. The AI-simulated review proposed 3 (H-11, open) | |
| T19 | Automatic reminder to the patient | 2 wd after the case enters "patient contacted", if still there (`config/outreach_policy.yaml`, rule `reminder_patient_contacted_2d`) | Assumed. Automatic messages are OFF until a clinical lead signs the policy | |
| T20 | Cap on automatic messages | 2 per week per case, automatic and approved drafts together (`outreach_policy.yaml`, `max_auto_messages_per_week`) | Assumed | |
| T21 | Working calendar | Monday to Friday are working days, Friday counts as a full day, no public holidays configured, time zone Asia/Dubai (`config/calendar.yaml`) | `docs/evidence.md` 8d (u.ae public-sector hours). Government clinics may close at 12:00 on Friday, so a 1 wd step starting Thursday afternoon is short | |

### A4. Priority tiers (urgent / soon / routine)

The tier orders the worklist and explains why; it changes no state, time limit, escalation or closure. A case takes the highest tier any rule gives any of its results; a result matching no rule gets the default tier, "routine". The bowel pathway has no priority rules. Every rule below is marked "PLACEHOLDER, needs clinician sign-off (H1)" in the file; "the repository cites no standard for any of them, so none is a guideline value" (`chronic_conditions.yaml` l.77-78). Units: blood pressure mmHg, HbA1c mmol/mol, glucose and LDL mmol/L, BMI kg/m2. Rows P1 to P10 are in `cervical_hpv.yaml` (P1) and `chronic_conditions.yaml` l.91-174 (P2 to P10). Blood-pressure rules fire if either number reaches its cut-off.

| # | Rule id | Current value | Source / rationale | Reviewer |
|---|---|---|---|---|
| P1 | `positive_cancer_screen` (cervical, l.82-90) | Positive abnormal HPV or cytology result: **urgent** | PLACEHOLDER: "no cervical ranking standard is in the repository"; "urgent" is a design choice, not a sourced timeline | |
| P2 | `bp_very_high` (chronic, l.91) | systolic >= 180 **or** diastolic >= 120: **urgent** | PLACEHOLDER | |
| P3 | `bp_high` (l.101) | systolic >= 160 **or** diastolic >= 100: **soon** | PLACEHOLDER | |
| P4 | `bp_raised` (l.111) | systolic >= 140 **or** diastolic >= 90: **routine** | PLACEHOLDER | |
| P5 | `hba1c_high` (l.121) | HbA1c >= 75 mmol/mol (9.0 %): **soon** | PLACEHOLDER | |
| P6 | `hba1c_raised` (l.130) | HbA1c >= 48 mmol/mol (6.5 %): **routine** | PLACEHOLDER | |
| P7 | `fasting_glucose_high` (l.139) | fasting glucose >= 11.0 mmol/L: **soon** | PLACEHOLDER | |
| P8 | `fasting_glucose_raised` (l.148) | fasting glucose >= 7.0 mmol/L: **routine** | PLACEHOLDER | |
| P9 | `ldl_raised` (l.157) | LDL cholesterol >= 4.1 mmol/L: **routine** | PLACEHOLDER | |
| P10 | `bmi_raised` (l.166) | BMI >= 30 kg/m2: **routine** | PLACEHOLDER | |
| P11 | Default tier and tier names | Tiers `urgent`, `soon`, `routine`; default `routine` when no rule matches (`cervical_hpv.yaml` l.77-78; `chronic_conditions.yaml` l.82-83). Is "routine" safe as the default for an unclassified result? Should the long-term-conditions pathway have tier-specific time limits (today an urgent and a routine result run the same 20 wd clock, l.41-42)? | PLACEHOLDER | |

### A5. Clinical definitions the pathway needs

| # | Item | Current value | Source / rationale | Reviewer |
|---|---|---|---|---|
| D1 | What counts as a **complete colposcopy** for closing a cervical case | A final report stating a complete colposcopy, or a completed procedure linked to one; the record must name the colposcopy. `search_codes: []` (no SNOMED or LOINC code asserted) (`cervical_hpv.yaml` l.52-62) | PLACEHOLDER: "adequacy, the transformation zone seen is a clinical definition this pack does not make" | |
| D2 | What counts as a **documented care plan** for closing a long-term-conditions case | A final report stating a complete "care plan visit": the visit happened and a care plan is documented (`chronic_conditions.yaml` l.54-64) | PLACEHOLDER: "its minimum content is a clinical definition this pack does not make" | |
| D3 | The "next step" wording in automatic patient messages (never a finding) | Bowel: "a camera test" (default). Cervical: "a follow-up examination" (`cervical_hpv.yaml` l.66-69). Long-term conditions: "a visit with your family doctor" (`chronic_conditions.yaml` l.68-71) | PLACEHOLDER (H1) for the English in the two newer packs | |

### A6. Count

35 rows: 21 timings (T1 to T21), 11 priority rows (P1 to P11, of which P2 to P10 hold the 12 numeric cut-offs: 6 blood pressure, 2 HbA1c, 2 glucose, 1 LDL, 1 BMI), and 3 definitions (D1 to D3). Rows marked sourced in whole or in part: T9 and T10. Everything else is assumed or a PLACEHOLDER.
