# Screening Closure, one page

**Live demo: https://34-18-228-79.sslip.io** (synthetic data, simulated time, one VM, no failover; judge logins are in the submission form).

Team Humans Decide, Challenge 05, "Closing the Screening-to-Care Loop". **Everything shown is synthetic data in simulated time. Nothing is diagnosed, no message reaches a phone, and no clinician, native Arabic speaker or buyer has reviewed anything.**

## The problem

In an audit of Abu Dhabi ambulatory clinics, only 30.5% of positive bowel-screening results had a follow-up colonoscopy (BMC Cancer 2021, 2015 to 2016 data). Nobody decided to lose them; each step assumed someone else had it. The standard now sets a clock (referral within 15 working days of releasing a result, DoH IFHAS V10), but nothing watches it.

## What is built, against the owner's five steps

Each step with its screen, API endpoint and the tests that prove it: [five-steps-evidence.md](five-steps-evidence.md).

One engine gives every actionable result a named owner, a working-day clock, an escalation ladder to named people, an append-only audit trail and a clinician-signed closure. Models draft and classify; a person approves, accepts, signs and calls. One screen each:

| Step | Screen | What you see |
|---|---|---|
| 1. Record the result | Case page, `/coordinator/<case>` | The result arrives, an owner is named, the clock starts; the timeline is in plain words. |
| 2. Prioritise higher-risk people | Today, `/coordinator/today` | What needs a person now, urgent first, each with a plain-language reason. Long-term-conditions thresholds are **placeholders** awaiting a clinician. |
| 3. Refer, with insurance cover in mind | Case page, referral panel | "Why this unit": the first provider whose network accepts the patient's plan, or a flagged out-of-network fallback. Networks are synthetic. |
| 4. Remind and navigate until they attend | Patient page, `/patient/<case>` | My care, reminders and who looked at my record, in English and Arabic (Arabic unreviewed). Messages are logged, never sent. |
| 5. Track the outcome | Dashboard, `/dashboard` | The outcome funnel from result recorded to outcome documented, and the cases at risk of being lost. |

Beneath all five: a case cannot close without evidence and a physician's own signature (property-tested and model-checked), a silent scheduler raises an alert, and AI cannot move a case, approve a message, change consent or close.

## Headline result (calibrated to published trials, not validated)

In a simulation on a synthetic cohort, colonoscopy within 180 days rises from 51.0% to 66.8%, a ratio of 1.31. That ratio was **fitted** to the pooled navigation-trial ratio of 1.27, so it shows the mechanism, not new evidence of effect; most held-out timing checks fail. On an Abu Dhabi-anchored baseline the gain is smaller (+8.2 points at 360 days). The labour target of 40% fewer staff minutes is **not met**: 19.7% across the programme, 36.0% where the system reaches a case, and wage-weighted staff cost per case rises. Detail: [results](../results/results.md), [validation](../results/validation.md).

## The three biggest things not done

1. **No human review.** No clinician has reviewed any threshold, timing or wording; no native Arabic speaker has reviewed the Arabic. The reviews on file are AI-simulated. The outreach policy is unsigned, so nothing is sent automatically. Every value still waiting for a clinician's signature, with its file and line, is in [thresholds-to-sign.md](../clinical/thresholds-to-sign.md).
2. **No real-world contact.** Impact is simulated; the system is not connected to Malaffi, Sahatna or UAE PASS; all data is synthetic. API memory grows about 45 MiB/h for an unknown reason; an automatic API restart is a mitigation, not a fix.
3. **No buyer.** No letter of intent exists and nobody has been contacted. The AED 2.0 million licence is an assumption. The sourced offer is a 90-day shadow run at about AED 106,000, which sends nothing to patients ([commercial front page](../commercial/README.md); the [offer](../commercial/pilot-offer.md) and a one-page [decision memo](../commercial/decision-memo.md)).

Independent AI re-scores (not human judges) and what is being fixed: [rescore.md](rescore.md).

## How to see it

- **Hosted demo: live since 6 November 2026** at https://34-18-228-79.sslip.io (Google Cloud, me-central1, one VM, no failover; synthetic data, simulated time). Judge logins are in the submission form, not in the repository. See the [README](../../README.md) and the [submission index](README.md).
- **Video:** a 3-minute recorded pitch on the five steps (no code on screen); linked in the submission form.
- **Locally:** Docker, Python 3.12, Node 22; the first run takes about half an hour (the demo seed alone is about 12 minutes), later runs about 5 minutes. Steps: "Judge path" in the [README](../../README.md). The live reply-triage model needs Apple-silicon macOS; elsewhere replies fail safe to a person.
- **Read next:** [assumptions and limitations](assumptions-and-limitations.md), [evaluation summary](evaluation-summary.md), [technical documentation](technical-documentation.md).
