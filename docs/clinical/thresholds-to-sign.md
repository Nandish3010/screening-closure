# Clinical thresholds and parameters to sign before any real use

Written 7 November 2026. **Nobody has signed any row below.** No clinician has reviewed any threshold, timing or wording, and no native Arabic speaker has reviewed the Arabic. The reviews on file ([clinician-review.md](../governance/clinician-review.md), [arabic-review.md](../governance/arabic-review.md)) are AI-simulated and are not sign-off. All data in the project is synthetic.

This is the one list of every placeholder or unsigned clinical value found in `config/`, `api/app`, `simulator/params.yaml` and `web/src`, with where it lives, who should sign it and what the system does until they have. "H1" is the clinical-lead review and "H2" the native Arabic review in [project/TRACKER.md](../../project/TRACKER.md). The "who signs" column is our proposal. Questions already put to the reviewers are the H-numbers in [clinician-review.md](../governance/clinician-review.md) section 5 ("Questions only a licensed local clinician (or DoH) can settle").

**What the system does until a row is signed, in general.** A priority tier only orders the worklist and the Today screen; it changes no state, clock, escalation or closure (`api/tests/test_priority.py::test_t_pr12_the_tier_changes_no_clock_and_no_state`, `::test_t_pr14_the_tier_is_in_no_decision_path`). Staff see a note on the tier badge, "Threshold is a placeholder pending clinician sign-off (H1)" (`web/src/lib/priority.ts:16`). A patient never sees the tier (`::test_t_pr11_a_patient_never_sees_the_priority`). Clocks run and escalate to named staff only; no escalation messages a patient. No automatic patient message is sent while the outreach policy is unsigned (`api/tests/test_outreach_unsigned.py::test_t_a73_the_append_guard_refuses_every_automatic_message_while_unsigned`), and no message reaches a phone in any case. A case cannot close without evidence and a physician's own signature, whatever any value below says.

## 1. Result-priority tiers (what ranks a person first)

Source of the tier rules: the pathway's `priority` section, applied once at intake. Only the tier and the rule's id are stored, never the result values. The repository cites no standard for any of these cut-offs, so none may be quoted as a guideline value. The bowel pathway has **no** priority section: it ranks nothing and holds **no FIT cut-off**; it takes the laboratory's positive flag (open question H-8, [clinician-review.md:361](../governance/clinician-review.md)).

| Name | Placeholder value | File:line | Who signs | Until signed |
|---|---|---|---|---|
| Hypertension, very high (tier urgent) | systolic 180 mmHg or more, or diastolic 120 mmHg or more | `config/pathways/chronic_conditions.yaml:91` (values 95-96) | Clinical lead (H1) with a primary-care physician | Ranks first on Today; orders only; "urgent" is a position in a list, not an emergency instruction, and no emergency advice is given (see safety-netting, section 4) |
| Hypertension, high (tier soon) | systolic 160 or more, or diastolic 100 or more | `config/pathways/chronic_conditions.yaml:101` (105-106) | same | same, ranked after urgent |
| Hypertension, raised (tier routine) | systolic 140 or more, or diastolic 90 or more | `config/pathways/chronic_conditions.yaml:111` (115-116) | same | same |
| Diabetes, HbA1c high (tier soon) | 75 mmol/mol or more | `config/pathways/chronic_conditions.yaml:121` (125) | Clinical lead (H1) with a primary-care physician or endocrinologist | same |
| Diabetes, HbA1c raised (tier routine) | 48 mmol/mol or more | `config/pathways/chronic_conditions.yaml:130` (134) | same | same |
| Diabetes, fasting glucose high (tier soon) | 11.0 mmol/L or more | `config/pathways/chronic_conditions.yaml:139` (143) | same | same |
| Diabetes, fasting glucose raised (tier routine) | 7.0 mmol/L or more | `config/pathways/chronic_conditions.yaml:148` (152) | same | same |
| Lipids, LDL cholesterol raised (tier routine) | 4.1 mmol/L or more | `config/pathways/chronic_conditions.yaml:157` (161) | same | same |
| Obesity, BMI raised (tier routine) | 30 kg/m2 or more; one cut-off, no age, ethnicity or tier above routine | `config/pathways/chronic_conditions.yaml:166` (170) | same | same |
| Result kinds, units and default tier | blood pressure in mmHg, HbA1c in mmol/mol, glucose and LDL in mmol/L, BMI in kg/m2; no rule matched gives "routine" | `config/pathways/chronic_conditions.yaml:83-89` | Clinical lead (H1) | A result in another unit must be converted before intake, or it is refused; no rule matched ranks as routine |
| Cervical: any positive screening result is urgent | `cancer_screen.positive = 1` gives tier urgent; no other tier | `config/pathways/cervical_hpv.yaml:82` | Clinical lead (H1) with a gynaecology or screening lead | Orders only, as above; the pathway badge says its timings are placeholders (`web/src/lib/pathway.ts:111`) |
| Bowel: FIT positivity cut-off and any ranking | None held: no `priority` section, no cut-off in code | `config/pathways/colorectal_fit.yaml` (absent) | Clinical lead (H1) | Every bowel case is ranked the same; the laboratory's positive flag is trusted |

## 2. Clocks and escalation (when someone is told a case is late)

SLAs count working days on the UAE calendar. Sourced clocks are marked; the rest are our own design values.

| Name | Placeholder value | File:line | Who signs | Until signed |
|---|---|---|---|---|
| Bowel per-state SLAs (design values, not sourced) | acknowledge 2, accept ownership 1, contact patient 3, patient acknowledges 5, book colonoscopy 5, clinician review 5, sign-off 3, re-route after a rejection 2 working days | `config/pathways/colorectal_fit.yaml:118-125` | Clinical lead (H1) and programme coordinator | Clocks run and escalate to named staff; shown as our design values in the docs |
| Cervical per-state SLAs (bowel values carried over) | same eight values | `config/pathways/cervical_hpv.yaml:26-33` | Clinical lead (H1) | same; the web labels the pathway's timings as placeholders |
| Long-term-conditions per-state SLAs (bowel values carried over) | same eight values | `config/pathways/chronic_conditions.yaml:28-35` | Clinical lead (H1) | same |
| Bowel: referral requested within 15 working days of the result (**sourced**, DoH IFHAS V10, [evidence.md](../evidence.md) section 8b) | 15 working days, counted from case creation to the first referral request | `config/pathways/colorectal_fit.yaml:146` | Clinical lead (H1) to confirm the counting: result release versus receipt, and request versus acceptance | Runs as written; unverified whether DoH reports it as an indicator |
| Bowel: colonoscopy within 31 working days of the first referral request (**sourced**, National Guideline Appendix 2, not IFHAS) | 31 working days; never restarts after a rejection. The guideline itself says 15 working days in clause 12.5.2 and 31 days in Appendix 3 | `config/pathways/colorectal_fit.yaml:151` | Clinical lead (H1); H-8 asks if local endoscopy capacity supports it | Runs as written |
| Cervical: referral within 15 working days | 15, taken from the bowel standard; applicability to cervical results unconfirmed | `config/pathways/cervical_hpv.yaml:45` | Clinical lead (H1) | Runs as written; labelled unconfirmed |
| Cervical: colposcopy within 31 working days of referral | 31, the bowel colonoscopy figure carried over; no cervical timeline is in the repository | `config/pathways/cervical_hpv.yaml:50` | Clinical lead (H1) with a gynaecology lead | Placeholder; labelled in the web |
| Long-term conditions: referral within 15 working days | 15, from the bowel standard; applicability to a referral to a family physician unconfirmed | `config/pathways/chronic_conditions.yaml:47` | Clinical lead (H1) | Runs as written; labelled unconfirmed |
| Long-term conditions: care plan visit within 20 working days of referral | 20, a design value; no primary-care standard is in the repository; the tier does not change it | `config/pathways/chronic_conditions.yaml:52` | Clinical lead (H1) with a primary-care physician | Placeholder; labelled in the web (`web/src/lib/pathway.ts:118`) |
| Escalation ladder | reminder to owner at 0 days after breach, supervisor at 3, programme coordinator at 7 | `config/pathways/colorectal_fit.yaml:157-159` | Programme coordinator and sponsor | Staff are notified; no patient is contacted by an escalation |
| Referral unanswered, then bounced | 3 working days | `config/pathways/colorectal_fit.yaml:163` | Programme coordinator and the receiving providers | Bounce is automatic and returns the case to its referrer |
| Re-offer after informed refusal | 12 months, assumed; the AI clinician review proposed 3 (H-11); nothing re-offers automatically | `config/pathways/colorectal_fit.yaml:174` | Clinical lead (H1) | The case only shows a due date; nobody is contacted |
| Lost to follow-up | no progress for 30 days, or a lost-contact flag | `api/app/metrics.py:41` | Clinical lead (H1) and programme coordinator | Feeds the dashboard's "at risk" list and the closure metrics only |
| Watchdog: open case nobody has acted on | 7 days | `api/app/config.py:58` (also `watchdog/main.py:32`) | Programme coordinator | Flags the case in red for staff; changes nothing |
| Working week | Saturday and Sunday weekend; Friday counted as a full day; no public holidays loaded | `config/calendar.yaml:5-11` | Programme coordinator | Clocks skip weekends only; holidays do not pause a clock |

## 3. What counts as closed (clinical definitions of "done")

| Name | Placeholder value | File:line | Who signs | Until signed |
|---|---|---|---|---|
| Bowel closure evidence | A final report stating a complete colonoscopy (or CT colonography after an incomplete one), or a completed procedure linked to it. Bowel preparation, histology and the follow-up plan are not checked (H-6) | `api/app/evidence.py:20-24` | Clinical lead (H1) with an endoscopist | Closure still needs this evidence and a physician's signature; nothing weaker closes a case |
| Cervical closure evidence | A complete colposcopy; adequacy and the transformation zone are not defined | `config/pathways/cervical_hpv.yaml:58-62` | Clinical lead (H1) with a gynaecology lead | Same guard; the definition of complete is the pack's placeholder |
| Long-term-conditions closure evidence | A family physician visit at which a care plan is recorded; the minimum content of a care plan is undefined | `config/pathways/chronic_conditions.yaml:60-64` | Clinical lead (H1) with a primary-care physician | Same guard |

## 4. Patient contact (what may be said, to whom, how often)

| Name | Placeholder value | File:line | Who signs | Until signed |
|---|---|---|---|---|
| Outreach policy approval | `approved_by: null`, status `pending_h1_clinical_lead_signoff`; the whole policy at template set version 3 is what the clinical lead signs | `config/outreach_policy.yaml:29-30` | Clinical lead (H1) | **No automatic message is sent.** Held messages are counted and logged once per case. The local compose demo can run on a labelled placeholder approval with an amber banner (`OUTREACH_DEMO_APPROVAL`); the hosted, Helm and Bicep settings turn it off (`deploy/gcp/vm/render_env.py:61`) |
| Automatic messages per patient per week | 2 | `config/outreach_policy.yaml:31` | Clinical lead (H1) | Not in force while unsigned |
| First reminder | 2 working days after the case enters "patient contacted" | `config/outreach_policy.yaml:47` | Clinical lead (H1) | Not in force while unsigned |
| Channel preference order | sms, then whatsapp, then letter, only where the patient has consented and the contact is valid | `config/outreach_policy.yaml:46-49` | Clinical lead (H1) and data protection lead | Not in force while unsigned |
| English patient wording (templates, version 3) | Result-ready, reminder and appointment-confirmation texts that state no finding | `api/app/ai/templates/en.yaml:10` | Clinical lead (H1) (H-12) | Nothing is sent; the wording is shown to reviewers in [clinician-pack.md](../review/clinician-pack.md) |
| Arabic patient wording (templates, version 3) | Same texts in Arabic, unreviewed | `api/app/ai/templates/ar.yaml:26` | Native Arabic reviewer (H2) | Same; the patient page labels the Arabic unreviewed |
| Forbidden-phrase list for drafted messages | The phrases a drafted message may not contain | `api/app/ai/forbidden_phrases.yaml:1` | Clinical lead (H1) (H-12) for English, H2 for Arabic | A draft containing one is replaced by a template; every draft still needs a person's approval |
| Next-step wording for long-term-conditions and cervical messages | "a visit with your family doctor" / "a follow-up examination" | `config/pathways/chronic_conditions.yaml:69-71`, `config/pathways/cervical_hpv.yaml:67-70` | H1 (English), H2 (Arabic) | Placeholder; nothing is sent |
| Follow-up call script | Four lines a person reads; Arabic unreviewed | `config/agency.yaml:18-28` | H1 (English), H2 (Arabic) | A person reads it; nothing is sent by the system |
| Safety-netting wording and emergency number | **Not written.** No wording exists in the system | none (open question H-10, `docs/governance/clinician-review.md:363`) | Clinical lead (H1) | The system gives patients no emergency advice; an "urgent" tier must not be read as one |

## 5. Reply triage (what the model may handle alone)

| Name | Placeholder value | File:line | Who signs | Until signed |
|---|---|---|---|---|
| Confidence threshold below which a person decides | 0.553 (fitted on synthetic replies only) | `api/app/triage/calibration.json:33` | Clinical lead (H1) and the model owner | Below it, or with no model, a person decides; a reply never closes or advances a case (`api/tests/test_replies.py::test_a_reply_never_closes_or_advances_a_case_in_any_state`) |
| Stricter threshold for Arabizi replies | 0.918; Gulf-dialect Arabic is always sent to a person | `api/app/triage/dialect.py:30` | Clinical lead (H1) and H2 | Same |
| Intents that always go to a person | opt_out, other, question | `api/app/triage/calibration.json:2-6` | Clinical lead (H1) | Same; the auto-handled intents are listed in the same file |

## 6. Outreach cadence in the simulator (used for modelling, not decisions)

These assumed values drive the simulated results. A clinician decides the real equivalents; the pilot replaces them with measurements.

| Name | Placeholder value | File:line | Who signs | Until signed |
|---|---|---|---|---|
| Reminders before an appointment | 7 days and 1 day before | `simulator/params.yaml:333` | Clinical lead (H1) | Simulation only; no reminder is sent |
| Patient outreach attempts | 3 | `simulator/params.yaml:334` | Clinical lead (H1) | Simulation only |
| Referral re-route attempts | 4 | `simulator/params.yaml:358` | Programme coordinator | Simulation only |
| Receiving provider's response time | 3 working days | `simulator/params.yaml:355` | Programme coordinator | Simulation only |

The simulator also holds about 85 other values graded "assumed, no public source" (behaviour rates, delays and shares of patients). They are modelling inputs, not clinical decisions, and are listed with ranges in [simulator/params.yaml](../../simulator/params.yaml) and [assumptions-limitations.md](../assumptions-limitations.md).

## How to sign a row

A signature is the clinical lead's name in the file, with the version bumped where a version exists (the outreach policy and templates). For tiers and clocks it is a commit that replaces `PLACEHOLDER` in the `basis` or `source` field with the guideline cited; the web note disappears when the basis no longer starts with "PLACEHOLDER". Do not change a value without the sign-off, and do not remove the note.
