# Evaluation results: baseline vs orchestrated

generated_at: 2026-10-17 18:51:33 UTC (this line is the only non-deterministic content; `results.json` is byte-for-byte reproducible)

All data are synthetic. Reproduce every number here with `make evaluate`; verify reproducibility with `make evaluate-check`.

- Seed 20261017, 20 replications x 2,000 cases x 360 days of follow-up, 1000 bootstrap resamples
- Parameters hash `39bbc121ac0d0f49`, pathway `colorectal_fit` v0.1 hash `024b31c50766f69d`, git commit `08d97c323227`
- SLA override: `complete_confirmatory` uses 31 working days (DoH KPI, `followup_target_working_days`) instead of 45 in the pathway YAML. Intervals are percentile bootstrap 95% CIs over replications; differences are orchestrated minus baseline, paired by replication (same patients and random numbers in both arms).

## Summary

Across 20 simulated replications of 2,000 synthetic positive-FIT cases, the orchestrated arm got 83% of patients to a confirmatory colonoscopy within 180 days, against 52% at baseline (+30.9 percentage points, 95% CI +30.4 to +31.4, which excludes zero). At 90 days 29% of orchestrated cases were still open against 62% at baseline. Among cases that closed, the median time to a signed, evidence-backed closure was 63 days orchestrated against 70 at baseline; the baseline figure only covers the 53% of cases that closed at all, so read it together with the closure share (83% orchestrated). 45% of baseline cases were lost without anyone being told, against 0% in the orchestrated arm, where lost or unreachable cases are flagged to a human and never closed automatically. These results come from a model, not a trial: 103 of 131 parameters are assumed or calibrated rather than measured, so the direction of the effect follows from the mechanisms while its size depends on those inputs. Rerun with `make evaluate` after changing any parameter in `simulator/params.yaml`.

## Arm comparison

| Metric | Baseline (95% CI) | Orchestrated (95% CI) | Difference (95% CI) |
|---|---|---|---|
| Colonoscopy completed within 90 days | 42.7% (42.3% to 43.1%) | 76.0% (75.8% to 76.4%) | +33.3pp (+32.9pp to +33.7pp) |
| Colonoscopy completed within 180 days | 52.1% (51.5% to 52.6%) | 83.0% (82.7% to 83.4%) | +30.9pp (+30.4pp to +31.4pp) |
| Colonoscopy completed within 360 days | 55.2% (54.6% to 55.8%) | 83.2% (82.9% to 83.6%) | +28.0pp (+27.5pp to +28.5pp) |
| Cases still open at 30 days | 99.8% (99.7% to 99.8%) | 99.5% (99.4% to 99.5%) | -0.3pp (-0.3pp to -0.2pp) |
| Cases still open at 60 days | 82.0% (81.8% to 82.3%) | 63.3% (62.9% to 63.7%) | -18.7pp (-19.1pp to -18.4pp) |
| Cases still open at 90 days | 62.4% (62.0% to 62.8%) | 28.8% (28.6% to 29.1%) | -33.6pp (-34.0pp to -33.2pp) |
| Time to safe closure, median (closed cases only) | 70.3 (70.0 to 70.6) | 62.9 (62.8 to 63.1) | -7.4 (-7.7 to -7.1) |
| Time to safe closure, p90 (closed cases only) | 143.2 (140.4 to 146.2) | 95.5 (95.0 to 96.0) | -47.8 (-50.9 to -45.0) |
| Cases closed (with evidence and human sign-off) by end of follow-up | 53.4% (52.8% to 54.0%) | 83.2% (82.9% to 83.6%) | +29.8pp (+29.3pp to +30.4pp) |
| Time to named owner, median | n/a | 5.2 (5.1 to 5.3) | n/a |
| Time to named owner, p90 | n/a | 9.1 (9.0 to 9.2) | n/a |
| Time to first patient contact, median | 12.4 (12.2 to 12.6) | 11.1 (11.1 to 11.2) | -1.3 (-1.4 to -1.1) |
| Time to first patient contact, p90 | 23.2 (22.9 to 23.5) | 17.3 (17.2 to 17.5) | -5.8 (-6.1 to -5.6) |
| Reopen rate (reopened / closed) | 3.0% (2.8% to 3.2%) | 3.1% (2.9% to 3.2%) | +0.1pp (-0.0pp to +0.2pp) |
| Escalation precision (acted on within 5 working days) | n/a | 48.2% (47.9% to 48.6%) | n/a |
| Escalations per 100 cases | 0.00 (0.00 to 0.00) | 99.09 (98.15 to 99.87) | +99.09 (+98.22 to +99.90) |
| Automation abstention rate (AI cut supplies this) | n/a | n/a | n/a |
| Lost to follow-up (no colonoscopy, no progress for 30+ days) | 44.5% (44.0% to 45.2%) | 16.8% (16.4% to 17.1%) | -27.8pp (-28.3pp to -27.3pp) |
| Lost AND never surfaced to a human | 44.5% (44.0% to 45.1%) | 0.0% (0.0% to 0.0%) | -44.5pp (-45.2pp to -44.0pp) |
| No colonoscopy by the delay-harm threshold (270 days) | 45.7% (45.2% to 46.3%) | 16.8% (16.5% to 17.1%) | -28.9pp (-29.5pp to -28.4pp) |
| Outreach sent after consent withdrawn | 0.4% (0.3% to 0.5%) | 0.0% (0.0% to 0.0%) | -0.4pp (-0.5pp to -0.3pp) |

Open = not currently closed; a closed case that is reopened counts as open again. Time to closure is conditional on closing within the follow-up window. Lost to follow-up = no confirmatory colonoscopy by the end of follow-up and either an explicit lost-contact flag or no pathway progress for 30+ days. `n/a` means the arm has no such events by design (the baseline has no named owner and no escalations) or no input from the AI cut yet.

## Calibration check: does the baseline reproduce published completion?

The baseline arm is the unmanaged process. Its completion curve was tuned (parameters with status `calibrated`) to the Mohl et al. 2023 curve for positive stool tests in US health systems. The orchestrated arm is compared with the actively managed Kaiser Permanente curve (Corley et al. 2017), which is the nearest published ceiling.

| Follow-up | Published unmanaged (Mohl 2023) | Simulated baseline | Gap | Published managed (Corley 2017) | Simulated orchestrated |
|---|---|---|---|---|---|
| 90 days | 43.0% | 42.7% (42.3% to 43.1%) | -0.3pp | 74.2% | 76.0% (75.8% to 76.4%) |
| 180 days | 51.0% | 52.1% (51.5% to 52.6%) | +1.1pp | 80.6% | 83.0% (82.7% to 83.4%) |
| 360 days | 56.0% | 55.2% (54.6% to 55.8%) | -0.8pp | 83.2% | 83.2% (82.9% to 83.6%) |

The baseline lands within 1.1 percentage points of the published curve at every horizon shown.
Among baseline completers the simulated time to colonoscopy is median 61 days (IQR 47 to 85). The Sea Mar FQHC figure in the parameter file (83 days, IQR 52 to 131) describes a slower, lower-completion setting (22.9% at 90 days); Mohl's higher 90-day completion implies a faster completer median, so the simulator follows the Mohl completion curve and does not try to match both.
The simulated orchestrated-to-baseline completion ratio at 180 days is 1.59, against a pooled risk ratio of 1.27 for patient navigation in published trials. Orchestration here bundles ownership, timers, escalation and human outreach, and the ratio is larger than the navigation figure because the unmanaged baseline loses many cases to missed hand-offs that a managed programme prevents; treat it as an upper-range illustration, bounded above by the managed-programme ceiling.

## Fairness: completion and time to closure by language and channel

### Language

| Language | Baseline completion | Baseline median days to closure | Orchestrated completion | Orchestrated median days to closure |
|---|---|---|---|---|
| ar | 52.1% (51.3% to 52.8%) | 70.9 (70.3 to 71.5) | 83.0% (82.4% to 83.4%) | 63.1 (62.9 to 63.3) |
| en | 60.4% (59.5% to 61.2%) | 69.1 (68.4 to 69.7) | 83.4% (82.8% to 83.9%) | 63.0 (62.7 to 63.2) |
| other | 53.2% (52.3% to 54.2%) | 72.1 (71.1 to 73.2) | 83.6% (82.9% to 84.3%) | 62.5 (61.8 to 63.1) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 8.9% (7.8% to 9.9%) | 2.5% (2.0% to 2.9%) | -6.4pp (-7.6pp to -5.3pp) |
| Median days to closure, max gap between groups | 4.2 (3.3 to 5.2) | 1.6 (1.1 to 2.1) | -2.7 (-3.7 to -1.7) |

### Preferred contact channel

| Preferred contact channel | Baseline completion | Baseline median days to closure | Orchestrated completion | Orchestrated median days to closure |
|---|---|---|---|---|
| letter | 55.4% (53.9% to 56.8%) | 70.8 (69.6 to 72.0) | 82.4% (81.4% to 83.5%) | 68.5 (67.6 to 69.3) |
| phone | 54.7% (53.6% to 55.7%) | 70.9 (70.0 to 71.9) | 84.1% (83.0% to 85.1%) | 61.2 (60.6 to 61.7) |
| sms | 55.4% (54.6% to 56.1%) | 69.9 (69.3 to 70.5) | 83.4% (82.9% to 83.8%) | 62.6 (62.4 to 62.9) |
| whatsapp | 55.1% (54.2% to 56.0%) | 70.3 (69.5 to 71.1) | 83.0% (82.5% to 83.5%) | 61.5 (61.1 to 61.9) |

| Max-gap statistic (largest minus smallest group) | Baseline | Orchestrated | Difference |
|---|---|---|---|
| Completion, max gap between groups | 5.1% (4.2% to 6.0%) | 4.2% (3.4% to 5.0%) | -0.9pp (-2.3pp to +0.4pp) |
| Median days to closure, max gap between groups | 4.4 (3.7 to 5.2) | 7.7 (6.9 to 8.5) | +3.3 (+2.3 to +4.3) |

Completion is the share completing the colonoscopy by the end of follow-up. Groups smaller than the `metrics.min_group_size` parameter are shown above but excluded from the max-gap.

## Messy-case handling

| Messy case | Cases per replication | Handled explicitly: baseline | Handled explicitly: orchestrated | Completion: baseline | Completion: orchestrated | Completion: orchestrated, unaffected cases |
|---|---|---|---|---|---|---|
| Wrong contact number | 165 | 0.0% (0.0% to 0.0%) | 94.4% (93.6% to 95.1%) | 21.5% (20.3% to 22.7%) | 77.5% (76.3% to 78.6%) | 83.8% (83.4% to 84.1%) |
| Patient switches provider mid-pathway | 70 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 14.4% (12.7% to 16.0%) | 70.4% (68.1% to 72.6%) | 83.6% (83.2% to 83.9%) |
| Duplicate result from a second lab | 120 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 54.0% (51.9% to 56.1%) | 82.3% (81.3% to 83.5%) | 83.3% (82.9% to 83.6%) |
| Conflicting ownership acknowledgements | 77 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 58.2% (56.2% to 60.3%) | 84.7% (83.2% to 86.3%) | 83.2% (82.8% to 83.5%) |
| Consent withdrawn after booking | 24 | 0.0% (0.0% to 0.0%) | 100.0% (100.0% to 100.0%) | 66.0% (62.2% to 70.0%) | 62.1% (58.7% to 65.8%) | 83.6% (83.3% to 84.0%) |

`Handled explicitly` means the system logged the specific handling event (contact recovered, transfer accepted by the new owner, duplicate merged, second ownership claim rejected by the state machine, consent review completed). The baseline has no such mechanism by design, so its rate is 0. A messy case counts only if it actually occurred in that arm (for example, a provider switch after the colonoscopy is booked needs no transfer and is not counted). Wrong contact numbers are not injected separately: they are the `wrong_contact_rate` patient attribute.

## Parameters

Parameter files: `simulator/params.yaml`. Status counts: 87 assumed, 16 calibrated, 13 sourced, 15 sourced-adjacent. `sourced` and `sourced-adjacent` values are from public documents (details and caveats in `docs/evidence.md`); `calibrated` values were tuned to the published baseline curve; `assumed` values have no public source.

| Parameter | Value | Status | Source |
|---|---|---|---|
| `acknowledgement_delay_days` | 2 | assumed | No public measurement of staff time-to-acknowledge a positive result. Policy anchors only: UAE National Guideline cl. 12.3 (result to participant < 15 working days), DoH IFHAS V10 s.4.9.2.1 (results documented within 10 days of initial visit). |
| `appointment.booking_lead_median_days` | 24 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 14, no public source |
| `appointment.booking_lead_sigma` | 0.6 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.5, no public source |
| `appointment.max_attempts` | 3 | assumed | TODO |
| `appointment.rebook_lead_median_days` | 7 | assumed | TODO |
| `appointment.reminder_days_before` | [7, 1] | assumed | TODO |
| `appointment.responsiveness_noshow_shift` | 0.5 | assumed | TODO |
| `baseline.channel_share.letter` | 0.5 | assumed | TODO |
| `baseline.channel_share.sms` | 0.5 | assumed | TODO |
| `baseline.default_language` | en | assumed | TODO |
| `baseline.noshow_self_rebook_median_days` | 30 | assumed | TODO |
| `baseline.noshow_self_rebook_prob` | 0.45 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.30, no public source |
| `calendar.holidays` | [] | assumed | TODO |
| `colonoscopy_completion_12m` | 0.56 | sourced | Mohl et al. 2023, 56.1% at 360 d (median organisation 53.4%, range 17.4-71.8%). https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `colonoscopy_completion_12m_integrated_ref` | 0.832 | sourced | Corley et al. 2017, 83.2% within 12 months. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `colonoscopy_completion_3m` | 0.43 | sourced | Mohl et al., JAMA Netw Open 2023;6(1):e2251384, 43.3% at 90 d, 32,769 stool-test positives, 39 US organisations. https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `colonoscopy_completion_3m_integrated_ref` | 0.742 | sourced | Corley et al., JAMA 2017;317:1631, 74.2% within 3 months of 81,518 FIT positives, Kaiser Permanente. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `colonoscopy_completion_6m` | 0.51 | sourced | Mohl et al. 2023, 51.4% at 180 d. https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `colonoscopy_completion_6m_integrated_ref` | 0.806 | sourced | Corley et al. 2017, 80.6% within 6 months. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `colonoscopy_never_completes_12m` | 0.44 | sourced-adjacent | Derived: 1 - 0.561 from Mohl et al. 2023. https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 |
| `delay_harm_threshold_days` | 270 | sourced-adjacent | Systematic review, Clin Gastroenterol Hepatol 2021 (PMID 33010414): colonoscopy 'should not be delayed beyond 9 months' after positive FIT. https://doi.org/10.1016/j.cgh.2020.09.048 . Corley JAMA 2017: first significant excess at 10-12 months, any CRC OR 1.48 (1.05-2.08), advanced stage OR 1.97 (1.14-3.42); >12 months OR 2.25 and 3.22. https://jamanetwork.com/journals/jama/fullarticle/2620087 |
| `dna_rate` | 0.119 | sourced-adjacent | Lam et al. 2021 standard-care arm, colonoscopy non-attendance 11.9%. https://doi.org/10.1111/jgh.15218 |
| `eligible_age_max` | 75 | sourced | UAE National Guideline s.3.4 and DoH IFHAS V10 Appendix (40-75). Ages 76-85 decided individually. https://www.isahd.ae/content/docs/Colorectal%20guideline%20Screening%20and%20Diagnosis_UAE%20National%20Guidelines.pdf |
| `eligible_age_min` | 40 | sourced | UAE National Guideline s.3.4 (ages 40-75); DoH IFHAS V10 Appendix (FIT, 40-75 years, annually); ADPHC 24 Sep 2019 (40 and older). https://www.doh.gov.ae/-/media/EE758C3B77314BC394F2479AEF803AA2.ashx |
| `fit_interval_months` | 12 | sourced | UAE National Guideline Standard 2 (FIT every year); DoH IFHAS V10; ADPHC 2019. The earlier 2-year interval (HAAD 2016) was superseded in 2019. |
| `fit_positivity_rate` | 0.07 | sourced-adjacent | Ding et al., Cancers 2022;14(4):1073, pooled positivity 7.28% across 93 studies, https://pubmed.ncbi.nlm.nih.gov/35205821/ ; AltaMed FQHC 7.0%, https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ |
| `followup_target_working_days` | 31 | sourced | UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed. 2022, Appendix 2: colonoscopy within 31 working days of referral after positive FIT (>90% acceptable, >95% desirable). https://www.isahd.ae/content/docs/Colorectal%20guideline%20Screening%20and%20Diagnosis_UAE%20National%20Guidelines.pdf |
| `gi_referral_made_rate` | 0.9 | sourced-adjacent | Sea Mar FQHC: 642 of 711 abnormal FIT (90%) had a GI referral. https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
| `gi_referral_non_attendance_abu_dhabi` | 0.25 | sourced-adjacent | Derived: 36.3% of the ~992 positives without colonoscopy were 'referred to GI, did not attend' (about 360 of 1,429 positives), Almansoori et al., BMC Cancer 2021;21:897. https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ |
| `handoff.baseline_rejection_notice_median_days` | 21 | assumed | TODO |
| `handoff.baseline_rejection_noticed_prob` | 0.55 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.40, no public source |
| `handoff.declined_reengage_prob` | 0.5 | assumed | TODO |
| `handoff.max_attempts` | 4 | assumed | TODO |
| `handoff.no_response_prob` | 0.04 | assumed | TODO |
| `handoff.rejection_reason_share.capacity` | 0.35 | assumed | TODO |
| `handoff.rejection_reason_share.missing_info` | 0.3 | assumed | TODO |
| `handoff.rejection_reason_share.patient_declined` | 0.2 | assumed | TODO |
| `handoff.rejection_reason_share.wrong_service` | 0.15 | assumed | TODO |
| `handoff.reroute_delay_median_days` | 2.0 | assumed | TODO |
| `handoff.response_delay_median_days` | 2.0 | assumed | TODO |
| `handoff.response_delay_sigma` | 0.7 | assumed | TODO |
| `handoff.response_sla_working_days` | 3 | assumed | TODO |
| `malaffi_integration_assumption` | HL7 v2 message feeds; FHIR R4 not confirmed | sourced | DoH Circular 2026/10, 19 Jan 2026: mandatory Malaffi minimum dataset listed as HL7 message types (ADT, PPR, ORU, OMP, RDS, RAS, VXU, SIU incl. future appointments). https://www.doh.gov.ae/-/media/84A213F71AA545D69E01A23A0A99C598.ashx |
| `messy.baseline_conflict_delay_days` | 10 | assumed | TODO |
| `messy.baseline_switch_delay_days` | 30 | assumed | TODO |
| `messy.baseline_switch_loss_prob` | 0.7 | assumed | TODO |
| `messy.conflicting_ack_rate` | 0.04 | assumed | TODO |
| `messy.consent_after_booking_rate` | 0.02 | assumed | TODO |
| `messy.duplicate_delay_max_days` | 10 | assumed | TODO |
| `messy.duplicate_result_rate` | 0.06 | assumed | TODO |
| `messy.rate_multiplier` | 1.0 | assumed | TODO |
| `messy.switch_window_days` | 60 | assumed | TODO |
| `metrics.escalation_precision_window_working_days` | 5 | assumed | TODO |
| `metrics.ltfu_idle_days` | 30 | assumed | TODO |
| `metrics.min_group_size` | 30 | assumed | TODO |
| `navigation_effect` | 1.27 | sourced | Pooled risk ratio for colonoscopy completion, patient navigation vs usual care, 8 navigation studies, moderate certainty, J Gen Intern Med 2026 (PMID 42618855), RR 1.27 (95% CI 1.10-1.46). https://doi.org/10.1007/s11606-026-10706-x |
| `navigation_time_reduction_days` | 15 | sourced-adjacent | Coronado et al., Cancer Epidemiol Biomarkers Prev 2021 (RCT, Kaiser NW): median time to colonoscopy 73 d navigation vs 88 d usual care. https://pmc.ncbi.nlm.nih.gov/articles/PMC9273475/ |
| `no_show_rate_by_channel.letter` | 0.119 | sourced-adjacent | Lam et al. 2021, standard-care arm (written appointment slip only): 11.9%. https://doi.org/10.1111/jgh.15218 |
| `no_show_rate_by_channel.phone_call` | 0.089 | sourced-adjacent | Set equal to SMS: Cochrane CD007458 (Gurol-Urganci 2013), text vs phone call RR attendance 0.99 (0.95-1.02). https://www.cochrane.org/evidence/CD007458_mobile-phone-messaging-reminders-attendance-healthcare-appointments |
| `no_show_rate_by_channel.sms` | 0.089 | sourced-adjacent | Lam et al., J Gastroenterol Hepatol 2021 (RCT, 2,225 booked colonoscopy patients, Hong Kong): 8.9% with SMS 7-10 d before. https://doi.org/10.1111/jgh.15218 |
| `orchestration.consent_review_delay_median_days` | 2.0 | assumed | TODO |
| `orchestration.contact_recovery_delay_median_days` | 2.0 | assumed | TODO |
| `orchestration.contact_recovery_prob.on_failure` | 0.4 | assumed | TODO |
| `orchestration.contact_recovery_prob.owner` | 0.25 | assumed | TODO |
| `orchestration.contact_recovery_prob.owner_supervisor` | 0.5 | assumed | TODO |
| `orchestration.contact_recovery_prob.programme_coordinator` | 0.7 | assumed | TODO |
| `orchestration.delivery_failure_detect_days.letter` | 7.0 | assumed | TODO |
| `orchestration.delivery_failure_detect_days.phone` | 0.1 | assumed | TODO |
| `orchestration.delivery_failure_detect_days.sms` | 0.25 | assumed | TODO |
| `orchestration.delivery_failure_detect_days.whatsapp` | 0.25 | assumed | TODO |
| `orchestration.escalation_effect_prob.owner` | 0.5 | assumed | TODO |
| `orchestration.escalation_effect_prob.owner_supervisor` | 0.85 | assumed | TODO |
| `orchestration.escalation_effect_prob.programme_coordinator` | 1.0 | assumed | TODO |
| `orchestration.ladder_exhausted_wait_working_days` | 5 | assumed | TODO |
| `orchestration.reaction_delay_median_days` | 1.0 | assumed | TODO |
| `orchestration.reaction_delay_sigma` | 0.6 | assumed | TODO |
| `orchestration.signoff_sla_working_days` | 3 | assumed | TODO |
| `orchestration.timer_miss_prob` | 0.02 | assumed | TODO |
| `orchestration.transfer_reestablish_days` | 5.0 | assumed | TODO |
| `orchestration.watchdog_effect_prob` | 0.9 | assumed | TODO |
| `orchestration.watchdog_idle_days` | 14 | assumed | TODO |
| `orchestration.watchdog_reaction_delay_days` | 2.0 | assumed | TODO |
| `patient_response.coordinator_route_response_rate` | 0.7 | assumed | TODO |
| `patient_response.dropout_residual_multiplier` | 0.25 | assumed | TODO |
| `patient_response.human_dropout_residual_multiplier` | 0.6 | assumed | TODO |
| `patient_response.language_mismatch_multiplier` | 0.8 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.6, no public source |
| `patient_response.response_delay_median_days.letter` | 6.0 | assumed | TODO |
| `patient_response.response_delay_median_days.phone` | 0.5 | assumed | TODO |
| `patient_response.response_delay_median_days.sms` | 1.5 | assumed | TODO |
| `patient_response.response_delay_median_days.whatsapp` | 1.0 | assumed | TODO |
| `patient_response.response_delay_sigma` | 0.9 | assumed | TODO |
| `patient_response.response_rate_by_channel.letter` | 0.85 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.55, no public source |
| `patient_response.response_rate_by_channel.phone` | 0.95 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.80, no public source |
| `patient_response.response_rate_by_channel.sms` | 0.92 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.70, no public source |
| `patient_response.response_rate_by_channel.whatsapp` | 0.93 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.75, no public source |
| `patient_response.self_presentation_median_days` | 120 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 60, no public source |
| `patient_response.self_presentation_prob` | 0.38 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.10, no public source |
| `patient_response.staff_call_response_rate` | 0.65 | assumed | TODO |
| `patients.channel_share.letter` | 0.15 | assumed | TODO |
| `patients.channel_share.phone` | 0.15 | assumed | TODO |
| `patients.channel_share.sms` | 0.4 | assumed | TODO |
| `patients.channel_share.whatsapp` | 0.3 | assumed | TODO |
| `patients.consent_withdrawal_declines_care_prob` | 0.3 | assumed | TODO |
| `patients.consent_withdrawal_prob` | 0.02 | assumed | TODO |
| `patients.consent_withdrawal_window_days` | 90 | assumed | TODO |
| `patients.dropout_prob_per_lapse` | 0.15 | assumed | TODO |
| `patients.language_share.ar` | 0.45 | assumed | TODO |
| `patients.language_share.en` | 0.35 | assumed | TODO |
| `patients.language_share.other` | 0.2 | assumed | TODO |
| `patients.provider_switch_prob` | 0.05 | assumed | TODO |
| `patients.refusal_prob` | 0.12 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.12, no public source |
| `patients.responsiveness_sd` | 0.8 | assumed | TODO |
| `referral_rejection_rate` | 0.05 | sourced-adjacent | Derived 4.6% (15 of 324 abnormal FIT had a referral problem, e.g. insurance authorisation), AltaMed FQHC, https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ . Range 1.2% (incomplete forms, Champlain GI central intake, https://pmc.ncbi.nlm.nih.gov/articles/PMC10872000/) to 8.48% (Saudi SMARC e-referral, https://pmc.ncbi.nlm.nih.gov/articles/PMC12647054/). |
| `reopen.after_closure_prob` | 0.03 | assumed | TODO |
| `reopen.lag_median_days` | 30 | assumed | TODO |
| `reopen.lag_sigma` | 0.6 | assumed | TODO |
| `reopen.resume_state` | owner_accepted | assumed | TODO |
| `simulation.intake_window_days` | 30 | assumed | TODO |
| `simulation.start_date` | 2026-01-05 | assumed | TODO |
| `sms_reminder_effect` | 0.25 | sourced-adjacent | Relative reduction in colonoscopy non-attendance, (11.9 - 8.9) / 11.9, Lam et al. 2021. https://doi.org/10.1111/jgh.15218 |
| `staff.delay_median_days.accept_ownership` | 2 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 1.0, no public source |
| `staff.delay_median_days.book_confirmatory` | 6 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 4.0, no public source |
| `staff.delay_median_days.clinician_review` | 4.0 | assumed | TODO |
| `staff.delay_median_days.close` | 2.0 | assumed | TODO |
| `staff.delay_median_days.contact_patient` | 5 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 3.0, no public source |
| `staff.delay_sigma` | 0.8 | assumed | TODO |
| `staff.forget_prob` | 0.015 | calibrated | Calibrated to reproduce the Mohl 2023 baseline completion curve (see docs/results/results.md); placeholder was 0.04, no public source |
| `time_to_colonoscopy_iqr_days` | [52, 131] | sourced-adjacent | Sea Mar FQHC, IQR 52-131 d (same source as the median). https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
| `time_to_colonoscopy_median_days` | 83 | sourced-adjacent | Sea Mar FQHC, completers, median 83 d. BMC Gastroenterol 2021, https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
| `working_week` | {working_days: [Mon, Tue, Wed, Thu, Fri], friday_half_day: True, friday_end: 12:00, weekend: [Sat, Sun]} | sourced | u.ae, Working hours in the public sector: federal government Mon-Thu 07:30-15:30, Fri until 12:00, Sat-Sun weekend since 1 Jan 2022; Abu Dhabi government adopted a similar system. https://u.ae/en/information-and-services/jobs/Sector-of-employment/working-in-uae-government-sector/working-hours-in-the-public-sector |
| `wrong_contact_rate` | 0.08 | sourced-adjacent | Derived 8.3% (59 of 711 abnormal FIT: provider unable to reach patient), Sea Mar FQHC, BMC Gastroenterol 2021, https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ |
