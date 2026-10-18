# Calibration of simulator parameters from open datasets (ticket B18)

Run on 2026-10-18. Four open datasets were downloaded and fitted by scripts in `tools/calibration/`; a fifth (AIHW, Australia) could not be downloaded. Eight parameters in `simulator/params.yaml` now have the status `empirical`. Nothing here replaces the Mohl 2023 baseline calibration, the Abu Dhabi anchors or the colonoscopy no-show base rates: the datasets are Scottish, English and Brazilian, and are used for **relative effects and shapes** only (see "What this does not show").

Reproduce: `make calibrate-fetch` (download, manifest), `make calibrate-fit` (writes `tools/calibration/fitted.json`), `make calibrate-check` (fixture tests, then re-fits from the local files and fails on any difference from `fitted.json` or from the `empirical` leaves of `params.yaml`). URL, download time, sha256, size and licence of every file: [`tools/calibration/manifest.json`](../../tools/calibration/manifest.json) and [data-provenance.md](../data-provenance.md).

## Summary: what changed in `simulator/params.yaml`

| Parameter | Before (status, value) | After (status, value) | Dataset and fit |
|---|---|---|---|
| `sms_reminder_effect` | sourced-adjacent, 0.25 (Lam 2021 RCT) | **empirical, 0.167** (95% CI 0.146 to 0.187) | no-show data, pooled logistic regression, standardised relative reduction |
| `appointment.responsiveness_noshow_shift` | assumed, 0.5 | **empirical, 1.12** | no-show data, random-intercept logistic regression, between-patient SD |
| `time_to_colonoscopy_managed_ref_median_days` (new) | n/a | **empirical, 39.9** | Public Health Scotland KPI 4, lognormal through two band shares |
| `time_to_colonoscopy_managed_ref_iqr_days` (new) | n/a | **empirical, [26.0, 61.1]** | same fit |
| `time_to_colonoscopy_lognormal_sigma` (new) | n/a | **empirical, 0.63** | same fit |
| `colonoscopy_uptake_after_positive_scotland` (new) | n/a | **empirical, 0.75** | PHS appendix A4 counts |
| `fit_positivity_rate_england_120ug` (new) | n/a | **empirical, 0.0171** | NHS England BCSP 2024-25 Table 5 |
| `colonoscopy_uptake_after_positive_england` (new) | n/a | **empirical, 0.76** | NHS England BCSP 2024-25 Table 12 |
| `time_to_colonoscopy_median_days`, `_iqr_days` | sourced-adjacent, 83, [52, 131] | unchanged; notes record that the lognormal form holds in Scotland | kept as the unmanaged baseline target (Sea Mar) |
| `colonoscopy_completion_3m / 6m / 12m` | sourced, 0.43 / 0.51 / 0.56 (Mohl) | unchanged; notes carry the Scotland and England cross-checks | Mohl stays primary |
| `fit_positivity_rate` | sourced-adjacent, 0.07 | unchanged; note carries England 1.71% | cut-off dependent, see below |
| `gi_referral_made_rate` | sourced-adjacent, 0.90 | unchanged; note carries the English automatic referral | |
| `appointment.booking_lead_median_days`, `booking_lead_sigma` | calibrated, 24 and 0.6 | unchanged; notes carry the bounds | bound only, see below |

The six new keys are reference values that the simulator does not read (like `colonoscopy_completion_*_integrated_ref`); only `sms_reminder_effect` and `responsiveness_noshow_shift` change a run. `simulator/params.conservative.yaml` overrides `sms_reminder_effect` with 0.0 (the lower end of its range), which is still below the fitted value, so its value is unchanged and only its note was updated.

`empirical` is a new status. `simulator/sim/params.py` now lists the allowed statuses, refuses any other, and requires `fit_script` and `dataset_sha256` (64 hex characters) on an `empirical` leaf. `fit_script` and `dataset_sha256` are new optional leaf keys the simulator ignores. One assertion in `simulator/tests/test_sim_scenarios.py` that pinned the upper-range `sms_reminder_effect` at 0.25 now pins 0.167.

## Headline before and after (make evaluate, seed 20261017, 20 replications x 2,000 cases x 360 days)

Only `sms_reminder_effect` and `responsiveness_noshow_shift` change the run. Colonoscopy completion, orchestrated minus baseline, literature-anchored scenario (the one to quote):

| Metric | Mohl 2023 target | Before | After |
|---|---|---|---|
| Baseline completion at 90 days | 43.3% | 42.7% | 41.9% |
| Baseline completion at 180 days | 51.4% | 52.1% | 51.1% |
| Baseline completion at 360 days | 56.1% | 55.2% | 54.1% |
| Orchestrated completion at 180 days (literature anchored) | | 66.3% | 64.9% |
| Orchestrated minus baseline at 180 days (literature anchored) | | +14.2 points | +13.8 points |
| **Completion ratio at 180 days (literature anchored; navigation RR 1.27)** | | **1.272** | **1.269** |
| Orchestrated completion at 180 days (upper range) | | 79.6% | 78.3% |
| Completion ratio at 180 days (upper range) | | 1.529 | 1.532 |
| Parameter hash, upper range / literature anchored | | `3d2d984bb4cb25aa` / `edb40020bea5b7b7` | `b90c557e531268dc` / `d48f20dc2668a41f` |

The ratio stays at 1.27 to two figures, so `orchestration.programme_reach` (0.55) was **not** refitted. The larger between-patient no-show spread (0.5 to 1.12) lowers every completion rate by about one point in both arms, which moves the baseline 0.2 to 1.1 points away from the Mohl targets at 90 and 360 days and 0.7 points closer at 180 days: the baseline stays within about one point of Mohl, but a deliberate re-tune of the calibrated baseline parameters (ticket B12 procedure) would be the owner's decision. The lower SMS effect (0.25 to 0.167) only touches the upper-range scenario (the literature-anchored overlay sets it to 0). The regenerated results of this run are not committed to `docs/results/results.*` on this branch, because another branch (cut-effort) also regenerates them; see "Knock-on effects" below.

## Knock-on effects the owner needs to decide on

Any change to a parameter value, source or status changes the parameter hash, and several committed artefacts record it. These were **not** regenerated here (outside this ticket's files, and cut-effort will touch the same ones), so on this branch:

- `docs/results/results.json`, `results.md`, `metrics.md` and the numbers quoted from them (technical documentation, pilot plan, the headline of 66.3% against 52.1% at 180 days) still show the previous parameters. `make evaluate` regenerates them (about 30 seconds on a quiet 10-core laptop); the table above is what they will show.
- The risk score records the parameter hash it was trained on: `simulator/tests/test_risk_training.py` (two tests) and `make risk-check` fail until `PYTHONPATH=api:. .venv/bin/python -m simulator.sim.export_training` (or `make risk-report`) is re-run. The test message says so; this is the drift guard working as designed.
- `docs/results/sensitivity.json` is the sweep of the old values and ranges (`simulator/tests/test_sensitivity.py::test_committed_json_is_the_sweep_of_the_current_parameters_and_ranges` and `make sensitivity-check` fail until `make sensitivity` is re-run).
- `simulator/sim/report.py` still describes four statuses in the results legend; it does not mention `empirical` (that file belongs to another branch).

## Dataset 1: Public Health Scotland bowel screening KPI workbook (OGL v3)

File: `2025-03-04-bowel-screening-kpi-report.xlsx`, 751,317 bytes, sha256 `a25716b4...`. Invites 1 May 2022 to 30 April 2024, data submission November 2024.

**Extracted** (by NHS board and Scotland, "all persons"): KPI 4 shares of people with a colonoscopy performed within 4 weeks, over 4 to 8 weeks and over 8 weeks of the screening-test referral date, with the reported median; appendix A6, the same in days (31, 62); appendix A4 counts of invites, positive tests and colonoscopies; KPI 3 positivity series. The workbook gives these by board and sex, not by year, so "by year" is not available beyond the positivity series (2.79% in 2021/23, 2.0% to 2.8% since 2007/09).

**Fit** (`fit_bands.py`): a lognormal time to colonoscopy with a point mass for people who never complete. Two cut points give two equations: sigma = ln(t2/t1) / (z2 - z1) and mu = ln t1 - z1 sigma with z = the normal quantile of the cumulative share. Scotland: 28.8% within 28 days and 70.4% within 56 days give median 39.9 days, sigma 0.633, IQR 26.0 to 61.1 days, 90th percentile 89.8 days. The fit uses only the band shares, so the **reported median (40.0 days) is an independent check** and agrees to 0.1 day. The appendix A6 bands (31 and 62 days) give median 39.9 and sigma 0.643. The point mass: 26,358 colonoscopies of 35,133 positive tests, so 25.0% had none in the window (an upper bound on "never", because the latest invitees had little follow-up time). Implied completion from the referral date, (1 - 0.25) x F(t): 67.6% by 90 days, 74.4% by 180, 75.0% by 360.

| NHS board | within 4 weeks | over 8 weeks | fitted median (days) | reported median (days) | fitted sigma | no colonoscopy in window |
|---|---|---|---|---|---|---|
| Ayrshire and Arran | 32.6% | 16.9% | 35.0 | 37.0 | 0.49 | 17.4% |
| Borders | 23.5% | 7.0% | 35.2 | 35.0 | 0.31 | 19.0% |
| Dumfries and Galloway | 74.8% | 2.4% | 19.6 | 22.0 | 0.53 | 18.3% |
| Fife | 68.7% | 8.7% | 19.0 | 24.0 | 0.80 | 20.1% |
| Forth Valley | 39.2% | 7.4% | 31.3 | 32.0 | 0.40 | 23.2% |
| Grampian | 2.1% | 84.8% | 113.0 | 109.0 | 0.68 | 31.5% |
| Greater Glasgow and Clyde | 20.0% | 38.4% | 46.8 | 49.0 | 0.61 | 24.8% |
| Highland | 29.7% | 17.3% | 36.0 | 36.0 | 0.47 | 24.4% |
| Lanarkshire | 20.2% | 48.2% | 54.1 | 55.0 | 0.79 | 28.1% |
| Lothian | 24.4% | 17.0% | 37.5 | 40.0 | 0.42 | 31.7% |
| Orkney | 43.8% | 19.0% | 31.1 | 33.0 | 0.67 | 20.4% |
| Shetland | 71.3% | 4.7% | 19.7 | 22.0 | 0.62 | 15.7% |
| Tayside | 31.9% | 10.9% | 33.9 | 34.0 | 0.41 | 18.4% |
| Western Isles | 68.6% | 6.4% | 20.3 | 24.5 | 0.67 | 22.2% |
| **Scotland** | 28.8% | 29.6% | 39.9 | 40.0 | 0.63 | 25.0% |

**Parameter changes.** Four new `empirical` reference keys: median 39.9 days, IQR [26.0, 61.1], sigma 0.63, uptake 0.75. The existing `time_to_colonoscopy_median_days` (83) and `_iqr_days` stay: they are the target for the **unmanaged** baseline arm (Sea Mar completers) and the simulator only compares its baseline to them in the report. Scotland is a managed national programme with a clock that starts at referral (a few days to two weeks after the result), so it is the reference for a well-run pathway, not for the baseline. The lognormal shape is validated by the independent median check and by the sigma agreement between the Scottish bands (0.63), the Sea Mar IQR (0.69) and the England waiting stock (below). Board medians range from 19 to 113 days, which is the real between-board spread that a programme-level median hides.

**Bounds on `appointment.booking_lead_median_days` (24) and `booking_lead_sigma` (0.6).** The booking lead is one part of the whole referral-to-colonoscopy time, so it cannot exceed it: 24 days is under the Scottish median of 39.9 days (which also contains the staff steps, about 13 days of medians in the simulator) and above the English all-indication median (below); 0.6 is under the Scottish total sigma of 0.63. Both stay `calibrated`; this is a consistency bound, not a refit.

## Dataset 2: AIHW National Bowel Cancer Screening Program monitoring report 2024: not obtained

The data page answered the command-line client with HTTP 403 and a browser with a Cloudflare "Verify you are human" challenge. Passing a bot-detection challenge is not done by this project, so no AIHW file was downloaded and **no parameter was derived from AIHW**. The cross-check of the lognormal against AIHW's median and 90th percentile, and of the 30/90/180/360-day follow-up rates against `colonoscopy_completion_*`, is therefore not done. For context only, a search summary (unverified, evidence grade S, not used in any fit) quotes 86% follow-up and a 62-day median from positive result to assessment for 2022 screening; if true, a 62-day Australian median is longer than the Scottish 40 days (referral clock) and shorter than the Sea Mar 83. Mohl stays the primary baseline calibration regardless. The owner can place the XLSX by hand in `data/external/aihw/` and a parser can then be written against the real layout.

## Dataset 3: NHS England bowel cancer screening annual report 2024-25 tables (OGL v3)

File: `Bowel_Cancer_Screening_Annual_Report_Tables_2024-25.xlsx`, 204,585 bytes, sha256 `c02d8b84...`. England rows of Tables 5, 11 and 12 (`fit_bcsp.py`).

| Quantity (England, 2024-25) | Count | Value | 95% interval |
|---|---|---|---|
| Uptake of those invited (S02) | 5,195,407 of 7,963,115 | 65.2% | |
| **Positivity**, further tests needed of those screened (S02) | 89,030 of 5,195,407 | **1.71%** | 1.70% to 1.72% |
| SSP clinic appointment offered within 14 days of referral (S06) | 90,888 of 91,396 | 99.4% | |
| First diagnostic test offered within 14 days of the SSP clinic (S07) | 63,937 of 71,671 | 89.2% | |
| Diagnostic test attended within 14 days of the SSP clinic (S08) | 35,185 of 69,826 | 50.4% | |
| **Diagnostic uptake after a positive FIT** (S11) | 69,956 of 92,107 | **76.0%** | 75.7% to 76.2% |

**Parameter changes.** `fit_positivity_rate_england_120ug` = 0.0171 and `colonoscopy_uptake_after_positive_england` = 0.76 (new, `empirical`, not read by the simulator). **`fit_positivity_rate` stays 0.07:** England's FIT cut-off is 120 ug Hb/g, far above the one used in Abu Dhabi, positivity moves by a factor of several with the cut-off (Netherlands 4.4% at 47, US programmes about 7% at 20), and the only Abu Dhabi measurement (13.5%, 2015-2016, cut-off not stated) is in the sensitivity range 0.018 to 0.135. The England value is the floor of that range, now measured rather than quoted. For `gi_referral_made_rate` (0.90, Sea Mar): England refers automatically, so the referral rate is about 1 there and the loss comes after it (24% of positives have no diagnostic test); the 2023-24 value was 76.0% too, so it is stable. The `colonoscopy_completion_12m` of 0.56 (Mohl) is the unmanaged US cohort the baseline arm represents; national managed programmes reach 0.75 (Scotland) and 0.76 (England), below the integrated Kaiser 0.83 that caps the orchestrated arm. This does not change the picture: Mohl stays.

## Dataset 4: NHS England Monthly Diagnostics (DM01), provider workbook, March 2026 (OGL v3)

File: `Monthly-Diagnostics-Provider-March-2026_5WSGC.xls` (legacy XLS, parsed by a stdlib BIFF8 reader), 1,113,088 bytes, sha256 `7ad11252...`. Colonoscopy rows of the "Provider by Test" sheet, summed over 229 provider rows: **78,391 patients waiting** at month end, in weekly bands 0 to 13+ weeks (band sums reproduce the workbook's totals for 6+ and 13+ weeks).

**Fit** (`fit_dm01.py`). DM01 gives the time waited **so far** by people still on the list. That is the age distribution of a queue, not the distribution of completed waits. For completed waits T lognormal(mu, sigma) the age density of the stock is S(a)/E[T] (the equilibrium distribution; the length-biased density t f(t)/E[T] describes the completed waits of the people in the stock), with the closed-form cdf G(x) = x S(x)/E[T] + Phi((ln x - mu - sigma^2)/sigma). A multinomial maximum-likelihood fit gives sigma 1.04 and a **median completed wait of 18.1 days** (mean 31.1, 90th percentile 68.5). Little's law gives an independent mean: 78,391 waiting / (54,488 waiting-list tests in the month / 31 days) = 44.6 days; the fit's 31 days is lower, as expected when the stock holds a long tail the single lognormal under-fits.

| Weeks waited | 0-1 | 1-2 | 2-3 | 3-4 | 4-5 | 5-6 | 6-7 | 7-8 | 8-9 | 9-10 | 10-11 | 11-12 | 12-13 | 13+ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Observed share | 20.9% | 15.3% | 11.5% | 9.1% | 9.0% | 5.4% | 3.2% | 2.7% | 3.3% | 1.9% | 1.6% | 1.5% | 1.3% | 13.2% |
| Fitted share | 21.0% | 15.8% | 11.6% | 8.7% | 6.7% | 5.3% | 4.2% | 3.4% | 2.8% | 2.4% | 2.0% | 1.7% | 1.5% | 12.7% |

The largest band error is 2.3 points (the 4 to 5 week bump, a scheduling effect no lognormal reproduces). 28.8% wait 6 weeks or more (the 6-week standard).

**Use: a bound only, no parameter set.** DM01 covers every colonoscopy referral in England (symptomatic, surveillance and screening), the clock starts at referral, and it says nothing about FIT-positive patients specifically. As a bound on the booking lead: the typical English colonoscopy wait (median about 18 days, mean 31 to 45 days) sits below the 24-day booking lead median plus the staff steps, and the Scottish screening median (39.9 days) above it, so 24 is in range but at the slower end of English diagnostic waits. Recorded in the notes of `appointment.booking_lead_*`.

## Dataset 5: Medical Appointment No Shows via OpenML (CC BY-NC-SA 4.0)

File: `Medical-Appointment-No-Shows.arff`, 10,994,060 bytes, sha256 `8f1a0dd7...`. 110,527 appointments, Vitoria (Brazil), 2016. **Licence: non-commercial, share-alike. Only fitted coefficients are published here, with attribution (J. Hoppen / Aquarela Advanced Analytics via OpenML dataset 43439); the records are never published or used in the product.** The licence is a third-party statement (the Kaggle page shows none).

**Rows kept** (`fit_noshow.py`): lead time (appointment date minus scheduling date) of at least 3 days: 60,017 appointments of 40,429 patients (no-show 29.7%; the whole file is 20.2%, short leads rarely no-show). Age outside 0 to 110 dropped.

**Pooled logistic regression** (Newton-Raphson): no-show on SMS_received, ln(lead days) and age (per decade, centred at 37).

| Term | Coefficient | Standard error |
|---|---|---|
| Intercept | -1.184 | 0.031 |
| SMS_received | -0.265 | 0.018 |
| ln(lead days) | 0.181 | 0.011 |
| Age, per decade | -0.095 | 0.004 |

SMS odds ratio 0.767 (95% CI 0.741 to 0.795). Standardised over the observed covariates, the **relative reduction in no-show probability is 0.167** (95% CI 0.146 to 0.187, delta method); the crude relative reduction is 0.156 (27.6% with an SMS against 32.7% without). This is `sms_reminder_effect`, a relative effect (the simulator multiplies its own colonoscopy base rate by 1 - 0.167). It lies inside the range already used (0 from Mahmud, 0.34 from the Cochrane review) and below Lam's 0.25.

**Between-patient spread** (patient-level empirical Bayes through a random-intercept model): 11,734 patients with at least two kept appointments (31,322 appointments); the marginal likelihood is maximised with 12-point Gauss-Hermite quadrature over the four coefficients and the intercept SD tau. Result: **tau = 1.12** on the logit scale (profile of the negative log-likelihood against tau: +154.8 at 0.675, +37.7 at 0.90, 0 at 1.125, +50.1 at 1.41, +247 at 1.80, so the estimate is tight given the model), conditional SMS odds ratio 0.774 (the pooled 0.767 is consistent). In the simulator the only source of between-patient no-show heterogeneity is the latent responsiveness factor, which has standard deviation 1, so `appointment.responsiveness_noshow_shift` (logit shift per unit of the factor) is exactly the between-patient SD: 0.5 (assumed) becomes 1.12 (empirical). `patients.responsiveness_sd` (0.8, scaling the same factor for outreach response) is unchanged.

**Not replaced:** `no_show_rate_by_channel` and `dna_rate` (0.089 to 0.119, Hong Kong colonoscopy RCT). The Brazilian rate is 30% at these lead times in primary care; it is not a colonoscopy no-show rate and is not used.

## What this does not show (honesty section)

- **Different populations.** Scotland and England are screening programmes with a different test, cut-off, invitation process and health system (50 to 74 or 60 to 74 years); Brazil is primary-care appointments. Nothing here was measured in Abu Dhabi or on FIT-positive colonoscopy patients, apart from the screening aggregates. Shapes (lognormal waits, sigma about 0.6) and relative effects transfer better than levels, which is why levels were not copied.
- **Relative effects only, and observational.** The SMS effect is an association after adjusting for lead time and age; reminders are not randomised, so who gets one (and when) can confound it. It is a cross-check, within the range of the trials, not evidence of a colonoscopy effect. The tau estimate comes from patients with repeat bookings at 3+ day leads, and series booked together can inflate it; the effect on the run is limited (about one point of completion) because the baseline is calibrated to Mohl.
- **Clocks differ.** PHS time starts at the referral date, DM01 at referral, Mohl and Sea Mar at the positive result. Completion shares are of those with follow-up in the window, which is censored for recent invitees.
- **No AIHW.** One of the five requested datasets could not be obtained.
- **DM01 is a bound.** All indications, a stock not a cohort, one month, a single lognormal that misses the 4-to-5 week bump.
- **Licence.** The no-show file is non-commercial and share-alike. Only coefficients leave the machine; a commercial pilot must not rely on them without checking the licence or replacing them with local data.
- **Model, not trial.** These changes make a few parameters empirical; 87 of 138 parameters are still `assumed` and 16 `calibrated`. The headline is still a model result, not a measured effect.
