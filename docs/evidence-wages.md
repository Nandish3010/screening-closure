# Evidence: UAE wage ratios for the cost model

Status: research note, 18 October 2026. Docs only. It replaces the assumed wage ratios (clinician 3x, specialist 4x a coordinator) used in `tools/pilot/inputs.yaml` and in `docs/pilot-plan.md` section 8 and K9 with ratios backed by published salary figures. It does not change any input; the merge that adopts the ratios does that. Nothing here is an official pay scale. Read the honesty note at the end before quoting any number.

## 1. Summary

- No official source was found: no DoH Abu Dhabi, MOHAP or federal (FAHR) salary table for these four roles is publicly available. All figures come from recruiter placement guides, salary aggregators and job boards, which disagree with each other by a factor of two or more.
- None of the three big agencies' guides (Hays, Michael Page, Robert Walters) published a usable healthcare table that could be read: Hays GCC 2024 and Robert Walters Middle East 2026 were downloaded and contain no healthcare salary rows; Michael Page UAE 2026 is behind a sign-in.
- Triangulated monthly gross pay (AED), Abu Dhabi where the source is Abu Dhabi, otherwise UAE-wide: coordinator about 6,500; registered nurse about 10,000; GP about 26,500; consultant gastroenterologist or consultant physician about 75,000.
- Implied ratios to the coordinator median: nurse 1.6, GP 4.1, consultant 11.5. The assumed clinician ratio of 3 is low but in the right region; the assumed specialist ratio of 4 is far too low (sourced-adjacent value about 10).
- Recommended set (status sourced-adjacent): coordinator 1.0, nurse 1.5, nurse navigator 2.0 (proxy, see 4), clinician (GP or owner physician) 4.0, specialist (consultant gastroenterologist) 10.0.
- The only published loaded hourly figure found is AED 71 per hour for outpatient nurses at Tawam Hospital (SEHA), 2018, with a method caveat (section 5). No loaded figure for a coordinator or for the UAE health system generally was found.

## 2. Monthly pay by role

All figures are AED per month, gross. "Base" or "package" is stated where the source states it; where it does not, the figure is marked unknown. Median here is either the source's own stated median/average or, where a source gives only a range, the midpoint of its range (marked "mid"). The "triangulated" row is the median of the source central values listed, computed by me; it is not published anywhere.

### (a) Care coordinator / patient navigator / medical administrative coordinator

| Source (year) | Scope | Low | Central | High | Basis | URL |
|---|---|---|---|---|---|---|
| Naukri Gulf, Patient Care Coordinator (2026, read via search snippet; page fetch timed out) | Abu Dhabi | - | 6,499 (most commonly reported) | - | Self-reported, 37 salaries (UAE-wide: range 3,000 to 10,000, average 6,108) | https://www.naukrigulf.com/salaries/patient-care-coordinator-salary-in-uae |
| Naukri Gulf, generic Coordinator (2026, snippet only) | Abu Dhabi | - | 5,210 average | - | 115 salaries; not medical-specific | https://www.naukrigulf.com/salaries/coordinator-salary-in-abu-dhabi |
| Indeed UAE, care coordinator (page updated 30 Sep 2026) | Abu Dhabi range, UAE average | 4,364 | 6,432 UAE average | 9,479 | "Average base salary"; only 6 salaries | https://ae.indeed.com/career/care-coordinator/salaries/Abu-Dhabi |
| Zavis job network, patient coordinator (2026) | UAE | 5,000 | 7,500 mid | 10,000 | Mid-experience band, gross before allowances; method not stated | https://www.zavis.ai/jobs/discipline/patient-coordinator |
| Indeed UAE, one employer (Invisalign Center) | UAE | - | 7,900 | - | 2 salaries; shown for context only | https://ae.indeed.com/cmp/Invisalign-Center/salaries |

Triangulated coordinator: low about 4,400, central 6,432 (median of 5,210, 6,108 to 6,499, 6,432, 7,500), high about 10,000. Used as 6,500 in the ratios below.

Not used: "Healthcare Administrator / Operations Manager" at 14,000 to 22,000 (entry) and 22,000 to 38,000 (mid) from MenaJobs (https://menajobs.me/industry-salaries/healthcare/uae). That is a managerial role, not a screening or referral coordinator. If the pilot's coordinators are in fact administrators of that grade, the ratios below would fall by roughly a third to a half.

### (b) Nurse

| Source (year) | Scope | Low | Central | High | Basis | URL |
|---|---|---|---|---|---|---|
| Health Bridge Medical Services, Healthcare Salary Guide UAE 2026 (published 25 May 2026) | Abu Dhabi (DoH) RN | 6,500 | 8,750 mid | 11,000 | Placement offer letters per the publisher; base or package not stated; FAQ on the same page says 7,000 to 15,000 | https://healthbridge.ae/resources/blog/healthcare-salary-guide-uae-2026/ |
| Glassdoor, RN Abu Dhabi (2026, via search snippet; page returned 403) | Abu Dhabi | 8,000 (total pay) | about 11,000 median total pay | 25,000 | Self-reported; base pay range 7,000 to 10,000 | https://www.glassdoor.com/Salaries/abu-dhabi-united-arab-emirates-registered-nurse-salary-SRCH_IL.0,30_IM953_KO31,47.htm |
| Naukri Gulf, RN Abu Dhabi (snippet only) | Abu Dhabi | - | 10,216 average | - | Only 6 salaries | https://www.naukrigulf.com/salaries/rn-salary-in-abu-dhabi |
| Indeed UAE, registered nurse Abu Dhabi (updated 11 Oct 2026) | Abu Dhabi | 1,104 | 4,285 average base | 16,639 | 691 salaries from postings; the low average suggests lower-paid and non-RN postings are mixed in; treated as a low outlier | https://ae.indeed.com/career/registered-nurse/salaries/Abu-Dhabi |
| MenaJobs Healthcare Salaries 2026 (updated May 2026, labelled AI-assisted) | UAE, Band 5/6 base | 8,000 entry | 15,000 mid-career mid | 22,000 senior | Cites Hays Healthcare MENA, Medacs, NES Fircroft, employer packages; ranges only | https://menajobs.me/industry-salaries/healthcare/uae |
| SearchPlus HR, UAE Healthcare Salary Guide 2026 (30 Mar 2026) | UAE, staff RN 2 to 5 years | 12,000 | 15,500 mid | 19,000 | Internal data and placements; base or package unclear | https://searchplushr.com/blog/uae-healthcare-salary-guide-2026-doctors-nurses-allied-professionals.php |

Triangulated RN: central 10,216 (median of 4,285, 8,500, 8,750, 10,216, 11,000, 15,000, 15,500); low about 6,500; high about 14,000 (about 22,000 for senior or specialised posts). Rounded to 10,000 in the text.

Nurse navigator / nurse case manager: no published salary found for Abu Dhabi or the UAE. Proxies only: nurse manager 14,500 (UAE listing average) and 16,427 (Naukri Gulf Abu Dhabi, 10 salaries); charge nurse 12,000 to 16,000 (Neelim, https://neelim.com/blog/nurse-salary-uae-guide, via search snippet). A navigator is more senior than a staff RN and below a manager, so 12,000 to 16,000 is a reasonable proxy band (about 1.8 to 2.5 times the coordinator). This is a judgement, not a sourced figure.

### (c) General practitioner / family medicine physician

| Source (year) | Scope | Low | Central | High | Basis | URL |
|---|---|---|---|---|---|---|
| Health Bridge, 2026 (25 May 2026) | Abu Dhabi (DoH) GP | 18,000 | 25,000 mid | 32,000 | Placement offer letters per publisher; "AED per month, tax-free" | https://healthbridge.ae/resources/blog/healthcare-salary-guide-uae-2026/ |
| RFS HR Consultancy, UAE 2025 (Q1 2025 update) | UAE GP | 18,000 | 28,000 | 40,000 | "Actual offer letters"; base or package not stated; bonus 5 to 10 percent | https://rfsonshr.com/salary-guide/healthcare/doctor-salary-uae/ |
| SearchPlus HR, 2026 | UAE GP 2 to 5 years | 25,000 | 31,500 mid | 38,000 | Internal data; unclear | https://searchplushr.com/blog/uae-healthcare-salary-guide-2026-doctors-nurses-allied-professionals.php |
| MenaJobs, 2026 | UAE GP, base | 22,000 entry | 33,000 mid-career mid | 48,000 senior | Ranges only | https://menajobs.me/industry-salaries/healthcare/uae |
| Tax Free Salaries aggregate, 2026 | UAE GP | 18,000 | 24,000 mid | 30,000 | Aggregates Hays GCC 2026, Michael Page 2026 and others as claimed; dates on the page conflict | https://taxfreesalaries.com/uae/salaries/ |
| Indeed UAE, general practitioner Abu Dhabi (updated 12 Oct 2026) | Abu Dhabi | 4,350 | 9,416 average base | 20,381 | 294 salaries from postings; low because small-clinic and non-specialist postings are mixed in; low outlier | https://ae.indeed.com/career/general-practitioner/salaries/Abu-Dhabi |

Triangulated GP: central 26,500 (median of 9,416, 24,000, 25,000, 28,000, 31,500, 33,000); low about 18,000 (about 10,000 at small private clinics, per a single GulfTalent Abu Dhabi posting at 10,000 to 12,000 inclusive of allowances, https://www.gulftalent.com/uae/jobs/general-practitioner-620010, snippet only); high about 40,000 or more for senior GPs.

### (d) Consultant gastroenterologist / specialist physician

| Source (year) | Scope | Low | Central | High | Basis | URL |
|---|---|---|---|---|---|---|
| Indeed UAE, gastroenterologist (updated 12 Oct 2026) | UAE-wide (Dubai highest, 74,558) | 58,322 | 73,573 average base | 92,811 | 282 salaries from postings over 36 months; the only gastroenterology-specific aggregator found | https://ae.indeed.com/career/gastroenterologist/salaries |
| Recruiter postings, Consultant Gastroenterologist (GulfTalent, via search snippet; pages returned 403) | UAE, mostly Dubai | 65,000 | - | 95,000 | Advertised, inclusive of fixed allowances; adverts, not paid pay | https://gulftalent.com/uae/jobs/consultant-gastroenterologist-431859 |
| Health Bridge, 2026 | Abu Dhabi (DoH), generic "Consultant" row, no gastroenterology | 55,000 | 72,500 mid | 90,000+ | As above | https://healthbridge.ae/resources/blog/healthcare-salary-guide-uae-2026/ |
| RFS HR Consultancy, UAE 2025 | Consultant physician 5+ years | 55,000 | 75,000 | 110,000 | Offer letters per publisher; bonus 15 to 20 percent | https://rfsonshr.com/salary-guide/healthcare/doctor-salary-uae/ |
| SearchPlus HR, 2026 | Consultant, General Surgery / Internal Medicine, 8+ years | 60,000 | 75,000 mid | 90,000 | Closest row; no gastroenterology | https://searchplushr.com/blog/uae-healthcare-salary-guide-2026-doctors-nurses-allied-professionals.php |
| MenaJobs, 2026 | Consultant physician, public sector (SEHA, DHA, SKMC, Tawam) | 60,000 | 90,000 mid | 120,000 | Proxy, not gastroenterology | https://menajobs.me/industry-salaries/healthcare/uae |

Triangulated consultant: central about 75,000; low about 55,000; high about 95,000 to 110,000 (premium private consultants are quoted at 80,000 to 180,000 in some guides, an upper tail not used). Specialist physician (non-consultant) is 35,000 to 70,000 across the same sources, and the gastroenterologist in a screening pathway doing colonoscopy is a consultant or senior specialist.

## 3. Summary table and implied ratios

Coordinator median taken as AED 6,500 per month (sources: 6,432 to 6,499).

| Role | Low | Median (triangulated) | High | Ratio to coordinator median |
|---|---|---|---|---|
| Coordinator | 4,400 | 6,500 | 10,000 | 1.0 |
| Registered nurse | 6,500 | 10,000 (10,216 computed) | 14,000 | 1.5 to 1.6 |
| Nurse navigator (proxy, not sourced) | 12,000 | 14,000 | 16,000 | 2.2 (range 1.8 to 2.5) |
| GP / family medicine | 18,000 | 26,500 | 40,000 | 4.1 (range 2.8 to 6.2) |
| Consultant gastroenterologist | 55,000 | 75,000 | 100,000 | 11.5 (range 8.5 to 15.4) |

Ratios computed from the low and high columns against the same coordinator median are shown as ranges. The spread is wide mainly because the coordinator wage is low in absolute terms, so small differences there move every ratio.

Comparison with the current assumptions: clinician assumed 3, evidence 4.1 (range 2.8 to 6.2); specialist assumed 4, evidence 11.5 (range 8.5 to 15.4).

## 4. Recommended ratio set

Status: sourced-adjacent. The figures come from published recruiter and aggregator pages for the right roles in the right country, but not from an official scale, with Abu Dhabi data for the GP and nurse rows only, and a proxy for the navigator.

| Role in the model | Ratio to coordinator | Basis |
|---|---|---|
| Coordinator | 1.0 | Reference; about AED 6,500 per month |
| Nurse (RN) | 1.5 | Median 10,000 to 10,216 against 6,500 |
| Nurse navigator | 2.0 | Proxy: charge nurse or senior band, 12,000 to 16,000; no direct source |
| Clinician (GP or owner physician) | 4.0 | Median about 26,500 (Abu Dhabi DoH GP midpoint 25,000, UAE aggregates 24,000 to 33,000) |
| Specialist (consultant gastroenterologist) | 10.0 | Median about 75,000 gives 11.5; rounded down to 10 because Abu Dhabi consultant bands (55,000 to 90,000) and public-sector bases sit below the Dubai-weighted job adverts |

One-line justification: nurse, GP and consultant pay medians from five or more independent-looking 2025 to 2026 UAE guides give 1.5, 4 and 10 to 11.5 times a roughly AED 6,500 coordinator; the specialist ratio is the one that matters most and was understated by more than half in the earlier assumption.

Suggested sensitivity range for the model: clinician 3 to 6, specialist 8 to 15, nurse 1.2 to 2.2.

Consequence to check when the merge happens: the earlier finding that weighted staff time per case is flat (84.6 against 85.0 coordinator-equivalent minutes) depends on the 3 and 4 ratios. Under the simulator the specialist minutes rise with the outcome (more patients reach colonoscopy) and coordinator minutes fall, so a specialist ratio of 10 instead of 4 will tilt weighted time per case upward, not flat. This note did not rerun the model.

Update, 18 October 2026 (integration merge): the recommended set was adopted in `tools/pilot/inputs.yaml` as `wage_ratio_nurse` 1.5, `wage_ratio_clinician` 4.0 and `wage_ratio_specialist` 10.0 (status sourced-adjacent, with the sensitivity ranges above), and `python3 tools/pilot/cost_model.py` now prints a wage-weighted staff-time section from the regenerated simulator minutes by role. The prediction held: weighted time per case rises, +10.5% at programme level in the literature-anchored scenario (125.5 coordinator-equivalent minutes at baseline against 138.7), +19.1% in reach, +17.1% in the upper range, and +5.8% to +17.1% at programme level across the clinician 3 to 6 and specialist 8 to 15 ranges. At the old 3 and 4 ratios the regenerated minutes give +1.8%.

## 5. Fully loaded hourly cost

One published figure was found; nothing for coordinators.

- Al Amiri N, El Khmidi S. Implementing Time-Driven Activity-Based Costing (TDABC) in out-patient nursing department: a case from UAE. Management Science Letters 9 (2019) 365-380. https://www.growingscience.com/msl/Vol9/msl_2019_40.pdf (read in full from the PDF). Outpatient nursing at Tawam Hospital, Al Ain (a SEHA hospital), data January and February 2018. The paper states a "predetermined cost rate per hour" of 1,487,900 AED divided by 21,008 hours, which is 71 AED per hour, applied to nurse hours.
- Caveats from the paper itself: the composition of "total nurse's cost" (salary, housing, benefits, overhead) is not stated. The paper covers 74 to 75 nurses, so 1,487,900 AED is about AED 19,800 per nurse per month, about twice the gross pay medians above, which suggests it includes allowances, housing and benefits, but this is an inference. The hours figure of 21,008 gives about 280 hours per nurse per month, more than a normal month of about 176 hours; if the true hours were 176 per nurse, the rate would be about AED 113 per hour. So the AED 71 is a lower-confidence, 2018, government-hospital outpatient nurse figure, not a market benchmark.
- Not loaded-cost evidence: home-nursing prices of AED 150 to 300 per hour in Dubai (patient-facing prices), AED 29 per hour "from" prices in Abu Dhabi, and hourly figures in salary aggregators (AED 17, 62 and 117 per hour for RNs in different pages, which disagree).
- The Abu Dhabi DoH requires clinical costing submissions (Abu Dhabi Clinical Costing Guidelines, https://www.doh.gov.ae/-/media/Feature/shafifya/Costing/Abu-Dhabi-Clinical-Costing-Guidelines.ashx) but no published nurse or coordinator cost-per-hour benchmark was found in them or in search results.

Observation for the model, not asked for: `coordinator_cost_aed_per_hour` is 120 in `inputs.yaml`. A coordinator at about AED 6,500 gross per month, even with a generous 1.5 to 2 times loading (the Tawam nurse ratio above), is about AED 9,750 to 13,000 per month, about AED 55 to 75 per hour on 176 hours. The 120 therefore looks high by roughly 1.6 to 2.2 times for a coordinator in the pay band found here, unless the model's coordinator is a nurse-coordinator. That is arithmetic on the figures above, not a sourced rate.

## 6. Honesty note on source quality

- No official source. DoH Abu Dhabi, MOHAP and the federal HR authority were searched; no public pay table for these roles was found. Government-hospital packages commonly include housing, so a gross figure from a recruiter may understate the public employer's cost by 25 to 35 percent (claim from RFS HR Consultancy, https://rfsonshr.com/salary-guide/healthcare/, not independently verified).
- Agency guides not read. Hays GCC Salary Guide 2024 and Robert Walters Middle East Salary Survey 2026 were downloaded and searched; neither contains healthcare salary rows. Hays GCC 2026 was not obtained (the 2024 edition was read instead). Michael Page UAE 2026 requires sign-in. A Scribd copy of the Michael Page 2025 guide was reported in a search snippet with consultant 40, 80, 100 and specialist 25, 42.5, 60, units unstated; I did not use it.
- Most of the figures come from blogs and aggregators run by recruiters or job sites, several of which cite the same big guides without reproducing them (the RFS page dated 2025 cites a "Michael Page UAE Salary Guide 2026"; Tax Free Salaries gives conflicting dates; MenaJobs labels itself AI-assisted). They may not be independent of each other, so the count of agreeing sources overstates the confidence.
- Several pages could not be fetched directly (Glassdoor, GulfTalent: HTTP 403; Naukri Gulf: timeouts). Those figures are from search-result snippets and are marked as such. They should be re-checked in a browser before any are quoted.
- Basis varies across sources: base, total pay including allowances, and advertised offers are mixed, and many sources do not say which. Indeed averages come from postings, include non-RN and non-specialist mixes (the nurse and GP Indeed averages are well below every other source), and were treated as low outliers.
- The coordinator row is the weakest: small samples (6 salaries on Indeed), no Abu Dhabi medical-coordinator survey, and a role definition that varies from receptionist-grade to nurse-coordinator. The ratios divide by this number, so it is the main source of error.
- The gastroenterology row is UAE-wide and Dubai-weighted; the Abu Dhabi consultant bands are lower. The nurse navigator row is a proxy.
- Net: use the ratios as order-of-magnitude weights for a cost model with a stated sensitivity range, not as quotes. The better route to a firm number is the provider's own payroll bands, or an offer from a paid benchmarking service (Hays and Michael Page both sell one), requested in the pilot's data requests.
