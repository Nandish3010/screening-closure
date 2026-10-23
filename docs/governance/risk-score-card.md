# Risk score card

Loss-to-follow-up risk score for the coordinator worklist (tickets D8, D9, D10). A model card in the sense of "what it is for, what it was trained on, how well it works, where it fails, how it is retrained". Related: [AI boundary](ai-boundary.md) (the language-model side), [decision matrix](decision-matrix.md), [training report](../results/risk/training.md), [regulatory mapping](regulatory-mapping.md).

**Status: a demonstration of the pattern, not a validated model.** It is trained on synthetic data produced by our own behaviour simulator, so it has learned the simulator's assumptions and nothing about real patients. Do not quote its numbers as evidence of clinical or operational performance.

**Language is not an input (D12).** The score does not read the patient's language, which is a proxy for nationality and must not rank patients (section 4, "Excluded features"). It is reported only as an outcome group in the fairness check (section 6). The model is never retrained on production data without Department of Health approval (section 8, "Retraining policy").

## 1. Intended use

One use: **ordering the coordinator's worklist**, and nothing else. It is a demonstration of the pattern (a transparent, calibrated, bounded risk score with a card, a boundary test and a reproducible training run), trained on synthetic data only. `GET /cases` carries `risk_score`, a number between 0 and 1: the modelled probability that the case ends with no colonoscopy within 180 days of the result. The worklist shows cases past their deadline first (SLA breach), and uses the score to order cases within that. `GET /cases/{id}` carries `risk` with the same score and the three features that moved it most, so a coordinator can see why a case ranks where it does.

The score is read by staff roles only. A patient's worklist rows and case page carry `null`.

## 2. Not for

- **Closing a case.** Closure needs evidence and a human clinician's sign-off, whatever the score says.
- **Escalating a case, raising or lowering an SLA, or starting any automated action.** The sweep and the watchdog do not read the score.
- **Any care decision**: whether, when or how a patient is investigated or treated, or how urgent a clinical need is.
- **Excluding a case from follow-up.** A low score does not make a case safe to leave; every open case has its owner, its timers and its ladder regardless.
- **Patient-facing use**, staff performance measurement, or allocating resources between providers.
- **Use on real patients** before it is retrained and validated on real data under the procedure in section 8.

### How the rule is enforced in code (T-D10)

The score is computed in one place and read nowhere. `app.risk` is imported only by `api/app/routers/cases.py`, and the name `risk_score` appears nowhere else in `api/app` as a read (an AST scan fails the build otherwise, `api/tests/test_risk_boundary.py`). Inside the router it is only assigned to the response. Dynamic proof: the same seeded world is run with the real score, with the model switched off, with a constant score and with a random score, and every case log, every read model and the order of the worklist come out identical (a hypothesis property test). A model that cannot be loaded leaves `risk_score` null and does not break the worklist. The API never sorts by the score: sort order is the web's concern.

**The agency (A22).** One agent of the agency, `sla_risk_explainer`, turns the top factors of the case page's explanation into a single sentence for staff (why a case is where it is on the worklist). It receives them through `factors_for_agency` in `api/app/routers/cases.py`, so the import rule above is unchanged, and the sentence is shown to a person and is not used by anything else. No agency plan, dispatch, task order or routing rule reads the score or the factors: a test replaces the function with random values and the plans and dispatches come out identical (T-A66, `test_t_a66_plans_and_dispatches_are_the_same_whatever_the_ordering_aid_says`). See [the agency](agency.md).

## 3. Training data

Everything is synthetic, from the behaviour simulator (`simulator/sim/`, [README](../../simulator/README.md)).

- **Scenario**: the orchestrated arm of the literature-anchored scenario (the one the results quote), 20,000 cases, seed 20261017, each followed for 180 days. 55% of its cases get the full orchestrated process (`programme_reach`) and 45% follow today's process, so the data contain both kinds of case.
- **Rows**: one snapshot per case, at a random day in the first 90 days after the result; what the worklist could have known on that day becomes the features. Cases whose colonoscopy is already done at the snapshot are not at risk and are left out (the score is 0.0 for them at serving time).
- **Label**: no colonoscopy within 180 days of the result.
- **Split**: 70% training, 30% held-out, assigned by a hash of the case key.
- **Parameters**: most simulator parameters are placeholders (88 of 132 are `assumed` at the time of writing; see [results](../results/results.md)). The relationships the model finds, for example that a withdrawn consent is the strongest predictor of loss, are the simulator's own assumptions played back.

The full provenance (parameter hash, row hash, coefficient hash, iterations) is in [`api/app/risk/model.json`](../../api/app/risk/model.json) and the [training report](../results/risk/training.md).

## 4. Features

All are known while the case is open and before a colonoscopy has happened. The same code ([`api/app/risk/features.py`](../../api/app/risk/features.py)) builds them from a simulated event log for training and from a case's read model and events for serving; a parity test feeds the same story in both event vocabularies.

| Feature | What it is | Where it comes from when serving |
|---|---|---|
| `stage` | Pathway stage before the colonoscopy: result received, acknowledged, owner accepted, patient contacted, patient acknowledged, confirmatory booked. A rejected referral counts as patient acknowledged, lost contact as patient contacted, consent withdrawn as the stage it interrupted. | The read model's state. |
| `days_since_result` | Days from the result to now, at most 90 | Genesis event time and the simulation clock. |
| `prior_escalations` | Escalations so far, at most 3 | `escalate` events. |
| `prior_bounces` | Handoffs rejected or timed out so far, at most 3 | `handoff_rejected` events. |
| `consent_withdrawn` | The patient's latest consent update withdraws consent | `consent_updated` events. |
| `provider_changed` | The case was transferred to another provider | A second `handoff_accepted`, or a `reassign`. |
| `contact_valid` | No known contact problem | The `contact_valid` flag on the case at creation. |
| `channel` | The patient's preferred channel (sms, whatsapp, phone, letter) | No registry feeds it yet: taken from the log when it shows it (`contact_patient.channel`), otherwise unknown. |
| `age_band` | 40-49, 50-59, 60-69, 70-75. Kept as the documented null feature (see below). | Unknown today. |

**Unknown values.** A feature that is unknown for a case is encoded at its training mean, so it adds exactly nothing relative to an average patient. Today the API knows nothing of the age band and little of the channel, so the served score rests on stage, days, escalations, bounces, consent, provider change and the contact flag. The performance table below includes the held-out result with channel and age band unknown, which is what the API serves today.

**Features that the brief or the pathway suggests and the simulator cannot support.** The simulator has no ages, so the age band is drawn by the exporter from its own random stream, independent of every behaviour: it is a null feature by construction. Its coefficients are about zero, which is the check that the pipeline does not invent signal. The simulator has no provider identities and no per-channel consent, so neither is a feature (`provider_changed` stands for the provider). Consent is the single withdrawal flag.

### Excluded features

| Excluded | Why |
|---|---|
| `language` | A proxy for nationality. A score that ranked patients partly by the language they speak would rank them partly by who they are, which a Department of Health reviewer would rightly object to, and the effect (a lower or higher place on the worklist) would fall on groups that already face barriers. It is therefore not a column of the model, not a field of `CaseFeatures`, and not read from the event log (`patient_acknowledge.lang` is ignored); a language entry in the serving attributes is ignored too. `Model` refuses a `model.json` that has a column for an excluded feature, and `top_factors` are built from the model's columns, so language cannot appear in them. The simulator still knows each patient's language and the exporter keeps it as an **audit label**, used only to report outcomes by group (section 6). Messaging in the patient's language is unaffected: that is a service the patient is entitled to, not a prediction input. |

The age band stays in, deliberately: it is the documented **null feature**, drawn by the exporter independent of every behaviour, so its coefficients should be about zero and the fairness table shows no real age effect. If it ever acquires weight, the pipeline is inventing signal. Real age would be a legitimate screening attribute, but the simulator has none to learn from, so nothing in this model should be read as an age effect. Channel stays in because it is a service attribute the programme chooses with the patient; it is audited (section 6) because it could correlate with access.

## 5. Performance

Measured on the held-out split of synthetic data. Generated by `python -m simulator.sim.export_training`.

<!-- risk:performance:begin -->
| Measure (held-out split) | Value |
|---|---|
| Held-out cases | 4588 (of 15321 rows from 20000 simulated cases) |
| Prevalence of loss to follow-up | 45.1% (training split 46.2%) |
| AUC | 0.8779 |
| Brier score | 0.1382 |
| Brier score of the base-rate predictor | 0.2477 |
| Brier skill score (1 minus the ratio) | 0.4420 |
| Expected calibration error (ECE, 10 equal-width bins) | 0.0342 |
| ECE of a perfectly calibrated model at this sample size (mean, 95th percentile) | 0.0136, 0.0187 |
| Tolerance: ECE at or below 0.05 | met |
| As served today, with channel and age band unknown (AUC, Brier, ECE) | 0.8777, 0.1384, 0.0344 |

The calibration tolerance is met: ECE 0.0342 is at or below 0.05, and the Brier score is better than the base-rate predictor.
<!-- risk:performance:end -->

The tolerance for calibration, ECE at or below 0.05 on the held-out set, was proposed in ticket D10 and confirmed after the first fit. ECE is the count-weighted mean absolute gap between the mean predicted probability and the observed rate over ten equal-width probability bins. The chance level shown is what a perfectly calibrated model would score at this sample size; the observed ECE sits just above it, with the gaps in the 0.5 to 0.8 bins, which is consistent with a linear-logit model that cannot represent the interaction between stage and days. Most of the discrimination comes from stage, days since the result and consent withdrawal: a case that stays early in the pathway for a long time is lost more often.

### Calibration

<!-- risk:calibration:begin -->
| Bin | Predicted range | Cases | Mean predicted | Observed rate | Absolute gap |
|---|---|---|---|---|---|
| 1 | 0.0 to 0.1 | 883 | 0.0500 | 0.0646 | 0.0145 |
| 2 | 0.1 to 0.2 | 437 | 0.1524 | 0.1304 | 0.0220 |
| 3 | 0.2 to 0.3 | 376 | 0.2519 | 0.2207 | 0.0312 |
| 4 | 0.3 to 0.4 | 673 | 0.3517 | 0.3076 | 0.0442 |
| 5 | 0.4 to 0.5 | 402 | 0.4467 | 0.4005 | 0.0462 |
| 6 | 0.5 to 0.6 | 286 | 0.5460 | 0.5245 | 0.0215 |
| 7 | 0.6 to 0.7 | 221 | 0.6504 | 0.7466 | 0.0962 |
| 8 | 0.7 to 0.8 | 259 | 0.7520 | 0.8456 | 0.0936 |
| 9 | 0.8 to 0.9 | 361 | 0.8527 | 0.8892 | 0.0365 |
| 10 | 0.9 to 1.0 | 690 | 0.9544 | 0.9406 | 0.0138 |

![Reliability diagram](../results/risk/calibration.svg)
<!-- risk:calibration:end -->

## 6. Fairness check by language and channel

Language is not an input, so this is an **outcome audit**: does the score treat language groups differently anyway, through the features it does use? For each group on the held-out split the table gives AUC and ECE (with the ECE a perfectly calibrated model would show at the same group size, "chance ECE", 95th percentile of 100 simulated draws), the mean predicted probability against the observed loss rate, and their gap. **A gap above 0.02 in size is flagged** (`FLAG`). The sampling margin column is how far the observed rate of a group of that size is expected to wander by chance alone (1.96 standard errors): a flagged gap inside its margin is not evidence of bias by itself, but it is still reported and has to be read. Channel is audited the same way, and age band is included as the null-feature check.

<!-- risk:fairness:begin -->
| Group | Value | Held-out cases | Prevalence | AUC | ECE | Chance ECE (95th percentile) | Brier | Mean predicted | Observed rate | Gap (predicted minus observed) | Sampling margin (95%) | Gap flag |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Language (audit only, not an input) | ar | 2111 | 46.5% | 0.8786 | 0.0283 | 0.0280 | 0.1376 | 0.4639 | 0.4652 | -0.0012 | 0.0213 | ok |
| Language (audit only, not an input) | en | 1563 | 41.1% | 0.8766 | 0.0410 | 0.0326 | 0.1377 | 0.4237 | 0.4107 | +0.0129 | 0.0244 | ok |
| Language (audit only, not an input) | other | 914 | 48.7% | 0.8760 | 0.0511 | 0.0435 | 0.1405 | 0.4827 | 0.4869 | -0.0042 | 0.0324 | ok |
| Preferred channel | sms | 1828 | 45.2% | 0.8761 | 0.0324 | 0.0303 | 0.1401 | 0.4558 | 0.4519 | +0.0039 | 0.0228 | ok |
| Preferred channel | whatsapp | 1348 | 44.9% | 0.8800 | 0.0409 | 0.0350 | 0.1367 | 0.4481 | 0.4488 | -0.0008 | 0.0266 | ok |
| Preferred channel | phone | 700 | 46.7% | 0.8776 | 0.0438 | 0.0470 | 0.1371 | 0.4678 | 0.4671 | +0.0007 | 0.0370 | ok |
| Preferred channel | letter | 712 | 43.7% | 0.8784 | 0.0414 | 0.0498 | 0.1375 | 0.4468 | 0.4368 | +0.0100 | 0.0364 | ok |
| Age band | 40-49 | 1266 | 45.6% | 0.8710 | 0.0276 | 0.0372 | 0.1425 | 0.4460 | 0.4558 | -0.0098 | 0.0274 | ok |
| Age band | 50-59 | 1271 | 44.1% | 0.8819 | 0.0316 | 0.0370 | 0.1358 | 0.4551 | 0.4406 | +0.0145 | 0.0273 | ok |
| Age band | 60-69 | 1290 | 46.4% | 0.8851 | 0.0458 | 0.0359 | 0.1347 | 0.4622 | 0.4643 | -0.0022 | 0.0272 | ok |
| Age band | 70-75 | 761 | 43.8% | 0.8699 | 0.0522 | 0.0468 | 0.1412 | 0.4514 | 0.4376 | +0.0138 | 0.0352 | ok |

Largest gap between groups (maximum minus minimum): language AUC 0.003, ECE 0.023; channel AUC 0.004, ECE 0.011; age band AUC 0.015, ECE 0.025.

Mean predicted probability against observed rate, flag threshold 0.02: language: no group flagged (largest gap 0.0129); channel: no group flagged (largest gap 0.0100); age band: no group flagged (largest gap 0.0145).
<!-- risk:fairness:end -->

How to read it. The score ranks cases about equally well in every language group (the AUC gaps are in the line under the table), calibration within each group is close to the chance level for its size, and the mean predicted probability of each language group sits within 0.02 of its observed loss rate: the score neither systematically under-predicts risk for Arabic speakers or for "other" language speakers, nor over-predicts it for English speakers, even though it never sees language. The observed rates do differ by language (the simulator gives groups different response probabilities); the score reproduces most of that difference through stage, days since the result, bounces and the like, which is how the outcome differs for real, not through the patient's language. This is a check of the pipeline, not of fairness in the world: the simulator contains no other source of inequity, and in a real population channel, postcode, provider and age could carry language indirectly, so the table must be repeated on any retrain and on shadow-mode data (section 8).

At the time of this card the age-band group 70-75 is the one flagged row (its gap is inside its sampling margin, and age is a null feature by construction, so it is chance, not signal). It is left flagged rather than tuned away: a flag is information, and a retrain that flags a language group is not accepted without an explanation (section 8, step 4).

**Change record (D12, language removed).** Same seed, same data, same fitting procedure; only the language columns differ. Before is the model committed at A19, after is the model committed at D12. Both predate the M5 recalibration of the simulator parameters (B18), after which the same procedure was retrained to the figures at the top of this card; the comparison is kept as the record of the language change.

| Held-out measure | Before (with language) | After (without) |
|---|---|---|
| AUC | 0.8938 | 0.8937 |
| Brier score | 0.1275 | 0.1276 |
| ECE | 0.0220 | 0.0193 |
| ar: AUC, ECE, mean predicted minus observed | 0.8972, 0.0278, +0.0018 | 0.8971, 0.0237, -0.0062 |
| en: AUC, ECE, mean predicted minus observed | 0.8922, 0.0245, -0.0008 | 0.8922, 0.0289, +0.0114 |
| other: AUC, ECE, mean predicted minus observed | 0.8857, 0.0344, -0.0083 | 0.8856, 0.0386, -0.0109 |

Removing language cost nothing measurable in discrimination or calibration (the language coefficients were small, at most 0.09 in log-odds), and moved the predicted mean of each language group by about one percentage point at most, toward the overall mean: English speakers are scored a little higher than before and Arabic and "other" speakers a little lower. The sign is worth watching. With language out, a group whose loss rate is genuinely higher is ranked slightly lower than a model that knew its language would rank it; that is the price of not ranking by nationality, and the answer to it is monitoring (section 8), not putting the proxy back.

## 7. Coefficients

Log-odds per column. Numeric columns are standardised on the training split, so their coefficients are per standard deviation. A positive coefficient raises the score.

<!-- risk:coefficients:begin -->
| Column | Coefficient (log-odds) | Odds ratio | Unit |
|---|---|---|---|
| intercept | 0.5769 | n/a | baseline logit for an all-zero row |
| channel=sms | 0.0561 | 1.058 | 1 if true, else 0 |
| channel=whatsapp | 0.0261 | 1.026 | 1 if true, else 0 |
| channel=phone | 0.0948 | 1.099 | 1 if true, else 0 |
| channel=letter | -0.1770 | 0.838 | 1 if true, else 0 |
| age_band=40-49 | -0.0416 | 0.959 | 1 if true, else 0 |
| age_band=50-59 | 0.0312 | 1.032 | 1 if true, else 0 |
| age_band=60-69 | -0.0299 | 0.971 | 1 if true, else 0 |
| age_band=70-75 | 0.0403 | 1.041 | 1 if true, else 0 |
| contact_valid | -0.1168 | 0.890 | 1 if true, else 0 |
| stage=result_received | 1.0334 | 2.811 | 1 if true, else 0 |
| stage=acknowledged | 1.1375 | 3.119 | 1 if true, else 0 |
| stage=owner_accepted | 0.6438 | 1.904 | 1 if true, else 0 |
| stage=patient_contacted | 1.1676 | 3.214 | 1 if true, else 0 |
| stage=patient_acknowledged | -0.5930 | 0.553 | 1 if true, else 0 |
| stage=confirmatory_booked | -3.3892 | 0.034 | 1 if true, else 0 |
| days_since_result | 1.4157 | 4.119 | per 1 SD (24.61; mean 38.12) |
| prior_escalations | 0.1067 | 1.113 | per 1 SD (1.11; mean 0.64) |
| prior_bounces | -0.0104 | 0.990 | per 1 SD (0.63; mean 0.22) |
| consent_withdrawn | 1.9825 | 7.261 | 1 if true, else 0 |
| provider_changed | -0.3259 | 0.722 | 1 if true, else 0 |
<!-- risk:coefficients:end -->

## 8. Limits and retraining

### Limits

- **Synthetic, and therefore not validated.** Nothing here says how the score behaves on real patients. A real pilot would measure it against outcomes (section 8).
- **The simulator's assumptions are the model's assumptions.** If a parameter changes, the relationships change; a test fails when the simulator parameters no longer match the ones the committed model was trained on.
- **Linear in the log-odds.** No interactions: the mid-range gaps in the calibration table are the visible cost.
- **Snapshots in the first 90 days only.** Cases older than that are scored with the days feature held at its cap.
- **A prediction of "no colonoscopy by day 180", not of harm.** Delay can harm before day 180, and a colonoscopy at day 179 counts as success.
- **No capacity, no clinical content.** Endoscopy slots, bowel preparation failures and findings are not modelled.
- **Feedback.** If the worklist order improves follow-up, the data that a retrain would see change. Retrain only from data collected under a documented ordering policy.
- **Missing attributes.** Channel and age band are unknown in the API today and imputed at the training mean.
- **Indirect proxies.** Removing language removes the direct proxy only. Other features (channel, and in a real registry postcode, provider, age) can still carry language or nationality; the audit in section 6 is the control, and it does not prove their absence.
- **Per-case explanation is not causal.** `top_factors` are contributions to the log-odds relative to an average training patient, not reasons the patient was lost.

### Retraining procedure

1. Change nothing by hand in `api/app/risk/model.json`. It is generated.
2. Make the change (simulator parameters, the feature set in `api/app/risk/features.py`, or the training settings in `simulator/sim/export_training.py`).
3. Run `make risk-report` (`PYTHONPATH=api:. .venv/bin/python -m simulator.sim.export_training` from the repository root). It reruns the simulator from the seed, refits, validates on the held-out split and rewrites `model.json`, `docs/results/risk/` and the generated blocks of this card. Two runs on the same inputs write identical bytes (T-D11); `make risk-check` (`... --check`) fails if a committed file differs from a fresh run (T-D12) and is a stage of `make check`.
4. Read the generated report. Accept the new model only if the calibration tolerance (ECE at or below 0.05) is met or explicitly waived in this card, the Brier score beats the base-rate predictor, AUC has not fallen without an explanation, the age-band coefficients are still about zero, the fairness table shows no group with a markedly worse AUC or an ECE well above its chance level, and no language group is flagged (mean predicted within 0.02 of observed) or any flag is explained here. `language` must stay out of `COLUMNS`; `api/tests/test_risk_features.py` and `simulator/tests/test_risk_card.py` fail if it comes back.
5. Update the prose in this card that depends on the numbers (the reading of the calibration table and of the coefficients), and add a line to `project/LOG.md`.
6. Run `make check`. The tests that guard the pattern are `api/tests/test_risk_*.py` and `simulator/tests/test_risk_training.py`.
7. Roll back by reverting the commit: the API reads `model.json` at start-up.

### Retraining policy

1. **Synthetic data only, until approved otherwise.** The model is trained on simulator output and on nothing else. It is **never retrained on production data without the approval of the Department of Health**, and then only on **consented, de-identified** data, as the DoH Responsible AI Standard restricts training on production data (clause 3.1.4.4 as read in [regulatory mapping](regulatory-mapping.md), section 4; verify against the current text). Consent for the programme's outreach does not by itself cover model training: the training use needs its own consent basis and its own approval.
2. **No training from the live system, even de-identified, on our own initiative.** The exporter reads the simulator, not the database. A change that connects it to the event store, the registry or any extract of real cases is a change of policy, not of code, and needs the approval in (1) first.
3. **Monitor in shadow mode instead.** In a pilot the score runs beside the process without changing it (it already cannot: section 2). Each month compare the score at the time a case first appears with its outcome at 180 days, in the calibration table, the AUC, and the mean-predicted-against-observed gap, per language group (language used as an audit label only, held outside the model), channel and age band. This produces evidence about the model without producing a training set, and without feeding the outcomes back into the model.
4. **What shadow monitoring may trigger.** A group whose ECE leaves its chance band, a language group flagged by the 0.02 rule that persists across two months, or a calibration tolerance miss: stop using the score for ordering until it is understood, and report it to the clinical safety owner. It does not trigger an automatic refit.
5. **Features are changed by decision, not by tuning.** Adding a feature (especially any that could proxy for nationality, language, religion, sex or area of residence) needs the fairness table for it and a line in this card first. `language` stays excluded.

### What a validated model would need

Before any score of this kind is shown to staff on real patients:

- **Approved real data**: consented, de-identified, extracted in the provider's environment inside the UAE, under DoH approval (policy 1), with a documented ordering policy during collection so that the data are not shaped by the model's own ordering.
- **Prospective or temporal validation**: a held-out period after the training period, on a different site if possible, with the pre-registered measures of this card (AUC, ECE at or below 0.05, Brier against the base rate, calibration per group, gap against the 0.02 rule). Retrospective fit on the training window is not validation.
- **Subgroup performance and bias review** with real attributes, including the indirect proxies listed under Limits, reviewed by the programme and the clinical safety owner.
- **Clinical safety case and sign-off**: hazard log entries for the score, the incident route, a named owner, and classification under the DoH risk protocol with conformity assessment if the classification requires it.
- **Impact evidence**: that the ordering improves follow-up without harming any group, measured against the process without it.
- **A change process**: versioned model, traceable training data hash, rollback, and a revalidation after any retrain.

None of these exists today. This card documents a pattern, not a validated model.

**Retraining on real data (not done).** If policy 1 is ever satisfied: replace the exporter's input with the approved extract of the same features and the same label, keep the split and the report, and treat it as a new model: a documented ordering policy during data collection, review of the fairness table by the programme, and sign-off by the clinical safety owner before the score is shown to staff. The training data must come from the provider's environment and never from anywhere else (see [regulatory mapping](regulatory-mapping.md)).
