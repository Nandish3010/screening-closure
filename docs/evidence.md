# Evidence for simulator parameters

Research compiled 2026-10-18. The sourced values were proposed in `simulator/params.proposed.yaml`, adopted by ticket B11 and folded into `simulator/params.yaml` at the M1 integration (the proposal file is removed; `params.yaml` is the single source).

## How parameters are graded

Each recommended value carries one of three grades.

- **sourced**: the number is taken directly from a public document that measured the same quantity in a comparable population, and the exact figure, page or table can be checked at the URL given.
- **sourced-adjacent**: the number comes from a public document that measured a closely related quantity, or the same quantity in a different setting (another country, another test, a general-outpatient rather than a screening population), or it is a simple derivation from published counts. The gap is stated in the "Recommended simulator value" line.
- **assumed**: no public source was found that a judge could check. The value is a placeholder and should be labelled as such in any submission.

A second marker, **Check**, records how I verified each row: **P** = I opened the primary source (full text, abstract on PubMed or Europe PMC, or the official PDF) and read the number there; **S** = the number comes only from a search-engine summary and I could not open the page (paywall, 403 or bot challenge). Treat **S** rows as leads to re-check before the submission, not as citable facts. Where a source contradicts itself internally I say so.

Two framing points that affect every section:

1. FIT positivity depends heavily on the haemoglobin cut-off (about 20 ug Hb/g in most US programmes, 47 in the Netherlands, 120 in England). Completion and delay figures also depend on whether follow-up is actively managed. There is no single "true" value, so the proposal gives a central value and a sensitivity range.
2. The Abu Dhabi context is now well anchored by official documents: the UAE National Guideline for Colorectal Cancer Screening and Diagnosis (3rd edition, 2022), the DoH IFHAS standard (V10, July 2025), DoH circulars 2025/42 and 2026/10, and one local peer-reviewed audit of 2015-2016 screening (Almansoori et al., BMC Cancer 2021). These are cited in section 8.

---

## 1. FIT positivity rate (programme level)

| Source | URL | Publication, year | Population | Number | Check |
|---|---|---|---|---|---|
| NHS England Bowel Cancer Screening standards data report 2023-24, section 2 and Table 2 | https://www.gov.uk/government/publications/bowel-cancer-screening-annual-report-2023-to-2024/bowel-cancer-screening-standards-data-report-2023-24 | NHS England, published 25 Feb 2025 | England, screening year 2023-24, ages 50-74 (FIT, 120 ug/g) | 83,112 people "further tests needed" of 4,710,330 adequate participants = **1.76%** | P |
| Evaluation of the Colorectal Cancer Screening Programme 2018-2021 (LECO for RIVM) | https://www.rivm.nl/sites/default/files/2023-05/Evaluation%20of%20the%20Colorectal%20Cancer%20Screening%20Programme%202018-2021.pdf | RIVM / LECO, Jan 2023 (Chapter 4, p. ~22; summary) | Netherlands, returned FIT 2018-2021, cut-off >=47 ug Hb/g | 270,547 positive = **4.4%** of returned tests | P |
| Scottish bowel screening programme statistics, invitations May 2022-Apr 2024 | https://publichealthscotland.scot/publications/scottish-bowel-screening-programme-statistics/scottish-bowel-screening-programme-statistics-for-the-period-of-invitations-from-may-2022-to-april-2024/ | Public Health Scotland, 2024 release | Scotland, ages 50-74 | **2.8%** of tests positive (35,133 referred) | P |
| Kaiser Permanente Northern California, program components and results | https://pmc.ncbi.nlm.nih.gov/articles/PMC7526597/ | Clin Gastroenterol Hepatol 2022 (PMID 33010408) | KPNC members 51-75, 2017, cut-off >20 ug/g | 14,450 positive of 419,940 FIT = **3.4%** | P |
| AltaMed FQHC mailed-FIT programme | https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ | c. 2022 | Los Angeles FQHC, 4,638 returned kits | 324 abnormal = **7.0%** | P |
| Ding et al., global performance indicators of FIT screening, systematic review and meta-analysis | https://pubmed.ncbi.nlm.nih.gov/35205821/ (Cancers 2022;14(4):1073, doi:10.3390/cancers14041073) | Cancers, 2022 | 93 studies, about 90 million person-times | pooled positivity **7.28%**; pooled invalid rate 1.08% | P (abstract via Europe PMC) |
| Almansoori, Alzaabi, Alketbi: CRC screening in ambulatory clinics in Abu Dhabi 2015-2016 | https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ | BMC Cancer 2021;21:897 | Emirati nationals 40-75, Abu Dhabi ambulatory clinics; FIT mostly, gFOBT when FIT unavailable (not split) | 13.5% of the 23.5% screened were positive (1,429 positives); no cut-off stated | P |
| Latin America systematic review (positivity by threshold) | https://scholarworks.indianapolis.iu.edu/items/7cfb5877-554c-481b-b91f-87813106df51 | 2024 | Latin American programmes | 15.2% at 50 ng/mL, 9.7% at 100 ng/mL | S |

**Recommended simulator value:** `fit_positivity_rate = 0.07` (sourced-adjacent), with a sensitivity range of 0.018 to 0.135. The pooled international estimate (7.28%) and a 20 ug/g US programme (7.0%) bracket the existing placeholder, and the only Abu Dhabi figure (13.5%, mixed FIT/gFOBT, 2015-2016, unknown cut-off) is the local upper scenario. Note that strict high-cut-off programmes (England, Netherlands, Scotland) sit at 1.8-4.4%, so 0.07 implies a sensitive cut-off.

---

## 2. Colonoscopy completion after a positive FIT (3, 6, 12 months and never)

| Source | URL | Publication, year | Population | Cumulative completion | Check |
|---|---|---|---|---|---|
| Mohl et al. | https://pmc.ncbi.nlm.nih.gov/articles/PMC9856942 | JAMA Netw Open 2023;6(1):e2251384 | 32,769 average-risk adults 50-75 with a positive stool test (FIT or mt-sDNA), 39 US health care organisations, index 2017 to mid-2020 | **43.3% at 90 d, 51.4% at 180 d, 56.1% at 360 d**; median organisation 53.4% (range 17.4%-71.8%); about 43.9% never within a year. The article does not report FIT alone. | P |
| Fendrick et al., "A Call to Action..." (reports the FIT-only split) | https://pmc.ncbi.nlm.nih.gov/articles/PMC10698770/ | Popul Health Manag 2023;26(6):448 | Same Optum data | 1-year: 56% overall, **48.7% after positive FIT**, 66.6% after mt-sDNA | P |
| Corley et al. | https://jamanetwork.com/journals/jama/fullarticle/2620087 | JAMA 2017;317(16):1631 | 81,518 patients with positive FIT, Kaiser Permanente N and S California 2010-2014 | **33.3% within 30 d, 63.6% within 2 mo, 74.2% within 3 mo, 80.6% within 6 mo, 83.2% within 12 mo** | P |
| Kaiser Permanente Northern California program | https://pmc.ncbi.nlm.nih.gov/articles/PMC7526597/ | CGH 2022 | 14,450 FIT-positive, 2017 | 42% within 30 d, 81% within 6 mo, **83.7%** (12,091) with diagnostic colonoscopy | P |
| PROSPR consortium (4 US systems) | https://doi.org/10.1158/1055-9965.EPI-15-0470 | Cancer Epidemiol Biomarkers Prev 2016;25(2):344 (PMID 26843520) | 62,384 aged 50-89, positive fecal blood test 2011-2012 | 12-month follow-up ranged **58.1% to 83.8%** across systems; most colonoscopies occurred within 6 months | P (abstract) |
| Sea Mar FQHC stepwise analysis | https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ | BMC Gastroenterol 2021 | 711 abnormal FIT, Washington State FQHC, 2017-2018 | **22.9% at 90 d, 37.1% at 180 d, 42.8% at 360 d**; funnel: 90% referred, 52% pre-procedure visit, 43% colonoscopy | P |
| STOP CRC secondary analysis | https://doi.org/10.1038/s41598-020-59032-0 | Sci Rep 2020 (PMID 32051454) | 2,018 abnormal FIT, 26 FQHC clinics | 52.8% within 12 mo | P (abstract) |
| Safety-net system, Los Angeles area | https://doi.org/10.1038/ajg.2016.555 | Am J Gastroenterol 2017 (PMID 28154400) | 2,238 positive FIT, 11 clinics, 2012-2015 | 55.6% within 12 mo; 13% never referred to GI | P (abstract) |
| AltaMed FQHC | https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ | c. 2022 | 324 abnormal FIT | 33.3% (108/324) within 12 mo. Internal inconsistency: Table 2 labels 122 as "within 1 year" (37.7%) | P |
| Abu Dhabi ambulatory clinics | https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ | BMC Cancer 2021 | 1,429 positive FIT/gFOBT, Emirati nationals, 2015-2016 | **30.5% (436 of 1,429) ever had follow-up colonoscopy** (49 of the 436 incomplete); "more than 50%" of positives had it within 90 d (text; no exact figure) | P |
| Dutch programme | RIVM link in section 1 | 2023 | 270,547 positives 2018-2021 | about 85% colonoscopy (no time window) | P |
| NHS England BCSP 2023-24 | gov.uk link in section 1, Table 9 | 2025 | England | 76.0% diagnostic test uptake after "further tests needed" (66,331 of 87,333) | P |
| Scotland 2022-24 | PHS link in section 1 | 2024 | Scotland | "three-quarters" had colonoscopy | P |
| Fendrick/Springer environmental scan | https://link.springer.com/article/10.1007/s11606-026-10630-0 | J Gen Intern Med 2026 | 16 studies | range 42%-81% within 180 d | S (page redirected) |

No 2023-2025 meta-analysis pooling the proportion completing colonoscopy after a positive FIT was found. The nearest are Ding 2022 (colonoscopy compliance reached 90% in only 7 of 69 studies, same PMID as section 1) and a 2026 intervention meta-analysis (section 5). Distribution therefore has to be assembled from cohorts.

**Recommended simulator value (baseline, unmanaged follow-up):** cumulative completion 3 months **0.43**, 6 months **0.51**, 12 months **0.56**, so about **0.44** never complete within 12 months (sourced: Mohl 2023, the only large cohort reporting all three windows; FIT-only 12-month sensitivity is 0.487). For an "integrated, actively managed" reference arm use Corley: 0.74 / 0.81 / 0.83. These two arms bracket the real-world range (local Abu Dhabi data, 0.31, is below the baseline).

Funnel stages worth modelling (Sea Mar, 711 abnormal FIT): GI referral made 0.90; of those referred, pre-procedure visit completed 0.57; colonoscopy 0.43 of all. The Abu Dhabi audit reports the dominant loss as "referred to GI but did not attend" (36.3% of non-completers, about 25% of all positives, derived from 360 of 1,429; the paper's text gives 933 as the denominator but the percentages fit about 992, so the 933 looks like a typo).

---

## 3. Time from positive FIT to colonoscopy

| Source | URL | Year | Population | Median (IQR) days | Check |
|---|---|---|---|---|---|
| Corley et al. | https://jamanetwork.com/journals/jama/fullarticle/2620087 | 2017 | Kaiser N and S California, completers | **37 (23-62)** | P |
| PROSPR | https://doi.org/10.1158/1055-9965.EPI-15-0470 | 2016 | 4 US systems | medians from **41 to 174** across systems | P |
| Sea Mar FQHC | https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ | 2021 | FQHC completers | **83 (52-131)**; by GI practice 75-112.5 | P |
| Safety-net system | https://doi.org/10.1038/ajg.2016.555 | 2017 | integrated safety net | **184 (140-232)** | P |
| AltaMed FQHC | https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ | c. 2022 | FQHC completers | 94; IQR given as 68-176 in abstract but 64-143 in Results | P (inconsistent) |
| Abu Dhabi ambulatory clinics | https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ | 2021 | Emirati nationals, 2015-2016 | **72**; the text says "interquartile range 114.25 days" (a single number, apparently a width); min 2, max 962 | P |
| Precision navigation RCT (KPNW) | https://pmc.ncbi.nlm.nih.gov/articles/PMC9273475/ | 2021 | abnormal FIT, completers | usual care 88, navigation 73 | P |
| LA County safety net navigation QI | https://pmc.ncbi.nlm.nih.gov/articles/PMC7901799/ | n/a | 2,531 abnormal FIT | median 121 (usual care) vs 105 (navigation) | P |
| STOP CRC, median 72 (49-120) | https://doi.org/10.1038/s41598-020-59032-0 | 2020 | 2,018 abnormal FIT | 72 (49-120) | S (not in abstract; unverified) |
| Scotland 2022-24 | PHS link, section 1 | 2024 | Scotland | 29% colonoscopy within 4 weeks of referral, 30% waited >8 weeks | P |
| NHS England BCSP 2023-24, Table 7 | gov.uk link, section 1 | 2025 | England | first diagnostic test attended within 14 days of referral 46.3% (31,229 of 67,416) | P |

**Recommended simulator value:** median **83 days**, IQR **52-131 days** (sourced-adjacent: Sea Mar FQHC completers). It sits between the integrated-system extreme (37 days) and the poorly integrated extreme (184 days), and is close to the only Abu Dhabi median (72 days). These are completers only; non-completers need the separate never-complete probability from section 2. A lognormal with median 83 and IQR 52-131 is a reasonable parametric form (this shape choice is an assumption).

---

## 4. Colonoscopy no-show / did-not-attend, and reminder channels

| Source | URL | Year | Population | Number | Check |
|---|---|---|---|---|---|
| Lam et al., SMS reminder RCT | https://doi.org/10.1111/jgh.15218 (PMID 32803820) | J Gastroenterol Hepatol 2021 | 2,225 adults booked for outpatient colonoscopy, 3 Hong Kong clinics | non-attendance **11.9% standard care (written slip) vs 8.9% with SMS 7-10 d before** (P=0.022) | P (abstract) |
| Mahmud et al., text messaging RCT | https://doi.org/10.1001/jamanetworkopen.2020.34553 (PMID 33492374) | JAMA Netw Open 2021 | 753 patients, urban academic endoscopy centre | attendance with good prep 53.1% vs 54.4%, P=.73; **no significant effect** of 9 texts | P (abstract) |
| Gurol-Urganci et al., Cochrane review CD007458 | https://www.cochrane.org/evidence/CD007458_mobile-phone-messaging-reminders-attendance-healthcare-appointments | Cochrane 2013 | 8 RCTs, 6,615 participants, general outpatient appointments | text vs none: RR attendance **1.14 (1.03-1.26)**, moderate certainty; text vs phone call: **0.99 (0.95-1.02)**; attendance 67.8% none, 78.6% text, 80.3% phone | P |
| Siriwardana et al., reminders QI | https://pmc.ncbi.nlm.nih.gov/articles/PMC13422818/ | BMJ Open Qual 2026 | 1,500 general-surgery clinic slots, UK | DNA 12.2% baseline, 9.4% phone, 8.1% text (sequential phases, not controlled) | P |
| Azulay et al., SMS reminders for positive FOBT | https://pmc.ncbi.nlm.nih.gov/articles/PMC6379817/ | JMIR mHealth 2019 | 470 aged 50-75 with positive FOBT, Israel | colonoscopy within 120 d **70.3% vs 47.1%**; OR 2.17 (1.49-3.17) | P |
| Abu Dhabi audit | https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ | 2021 | 1,429 positives | referred to GI but did not attend = 36.3% of those without colonoscopy; "scheduled but did not undergo procedure" 9.3% | P |
| Sea Mar FQHC | https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ | 2021 | 711 | patient declined / no-show / cancelled 19.2% of 402 non-completers | P |
| NHS England Hospital Outpatient Activity | https://digital.nhs.uk/data-and-information/publications/statistical/hospital-outpatient-activity/2024-25 | 2025 | all outpatients | DNA 5.9% (2023-24), 5.6% (2024-25), 6.2% (2019-20) | S (403) |
| PennMedicine/other Penn texting claim (62% vs 90%) | n/a | n/a | n/a | only a news summary seen; not used | S |

No published estimate of colonoscopy no-show by channel (SMS versus phone call versus letter) was found. The existing placeholders (0.30 / 0.20 / 0.40) therefore have no direct support.

**Recommended simulator value:** treat these as non-attendance at a booked colonoscopy: `phone_call` **0.089**, `sms` **0.089**, `letter` **0.119** (sourced-adjacent: Lam 2021 Hong Kong, general outpatient colonoscopy not FIT-positive; phone set equal to SMS because the Cochrane review found no difference, RR 0.99; "letter" is the written-slip standard-care arm). Model the SMS effect as a **relative reduction of 0.25** in non-attendance (Lam), with sensitivity 0 (Mahmud, null) to 0.34 (Cochrane-derived: 21.4% text vs 32.2% none non-attendance). The much larger loss in screening pathways happens before the appointment (non-referral, referred but never attended GI), see section 2.

---

## 5. Patient navigation effect after an abnormal screening result

| Source | URL | Year | Population | Effect | Check |
|---|---|---|---|---|---|
| Coronado et al., navigation RCT | https://pmc.ncbi.nlm.nih.gov/articles/PMC13007779/ (doi:10.7326/ANNALS-24-01885) | Ann Intern Med 2025;178(5):645 | 967 aged 50-75 with abnormal stool test, 32-clinic FQHC, Washington State | 12-month colonoscopy **55.1% vs 42.1%**, risk difference 13.0 pp (6.5-19.4), adjusted OR 1.69 (1.30-2.19); mean time 229 vs 256 d | P |
| Coronado et al., precision navigation RCT | https://pmc.ncbi.nlm.nih.gov/articles/PMC9273475/ | Cancer Epidemiol Biomarkers Prev 2021 | 415 analysed, Kaiser Permanente NW, predicted colonoscopy probability <=70% | 6-month **76% vs 65%**, HR 1.35 (1.07-1.72); median 73 vs 88 d; no-show 4.5% vs 7.4% | P |
| Meta-analysis, J Gen Intern Med 2026 | https://doi.org/10.1007/s11606-026-10706-x (PMID 42618855) | 2026 | 10 studies (8 navigation) in health-disparity populations | navigation **RR 1.27 (1.10-1.46)**, moderate certainty; 6 mo RR 1.29 (0.97-1.71); 12 mo RR 1.34 (1.20-1.49), I2 0% | P (abstract) |
| Selby et al., systematic review | https://doi.org/10.7326/M17-1361 (PMID 29049756) | Ann Intern Med 2017;167(8):565 | 23 studies | moderate evidence for navigators and provider reminders; absolute differences from -7.4 to +25 pp; no meta-analysis | P (abstract) |
| LA County safety net QI (pre/post, not randomised) | https://pmc.ncbi.nlm.nih.gov/articles/PMC7901799/ | n/a | 2,531 abnormal FIT | 6-month completion 40.6% to 46%. The paper's odds ratios (3.98) are not consistent with these percentages; not used | P |
| Azulay SMS (above) | https://pmc.ncbi.nlm.nih.gov/articles/PMC6379817/ | 2019 | positive FOBT | 70.3% vs 47.1% at 120 d | P |
| Rural Medicaid cluster RCT | https://jamanetwork.com/journals/jamanetworkopen/fullarticle/2831644 | JAMA Netw Open 2025 | only 34 with abnormal FIT | 43.3% vs 15.4% (imprecise) | S |

**Recommended simulator value:** `navigation_effect` = **relative risk 1.27** on the probability of completing colonoscopy (sourced: 2026 meta-analysis, moderate certainty), capped at the integrated-system ceiling (about 0.83), plus a **15-day reduction in median time to colonoscopy** (88 to 73 days, KPNW RCT; sourced-adjacent, one trial). The 12-month RCT result (+13 pp from a 42% base, RR about 1.31) agrees.

---

## 6. Referral rejection / returned referrals

| Source | URL | Year | Population | Number | Check |
|---|---|---|---|---|---|
| Alharbi et al., Saudi SMARC national e-referral | https://pmc.ncbi.nlm.nih.gov/articles/PMC12647054/ (doi:10.3389/fmed.2025.1665256) | Front Med 2025 | 62,206 paediatric e-referrals, Saudi Arabia, 2023-2024 | **8.48% rejected** (5,277), 91.52% accepted; reasons not reported | P |
| Monod et al., Champlain GI Central Intake (Ontario) | https://pmc.ncbi.nlm.nih.gov/articles/PMC10872000/ | J Can Assoc Gastroenterol 2024;7(Suppl 1):118 | 1,942 colonoscopy referrals, Aug 2022-Aug 2023 | 36.2% declined, but mostly "did not meet criteria" (19.3%), duplicates (4.3%); **incomplete forms 1.2%** | P (conference abstract) |
| AltaMed FQHC | https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ | c. 2022 | 324 abnormal FIT | "problem with referral (e.g. insurance authorisation)" in 15 of 231 without colonoscopy = about **4.6%** of all abnormal FIT (derived) | P |
| Pediatric incomplete specialty referral | https://doi.org/10.1016/j.jpeds.2010.07.012 | J Pediatr 2011 | 577 children | 30.2% incomplete (referral not resulting in visit); 10%-73% by specialty. This is non-completion, not rejection | P (abstract) |
| Ottawa ED outpatient referrals | https://doi.org/10.1155/2019/5179081 | 2019 | 963 patients | 16.6% lost to follow-up; 3%-50% by clinic. Non-completion, not rejection | P (abstract) |
| NHS joint guidance on e-RS | https://digital.nhs.uk/services/e-referral-service/joint-guidance-on-the-use-of-the-nhs-e-referral-service | NHS Digital | England | "about 2%" of referrals returned by providers as clinically inappropriate | S (page behind a bot challenge) |
| Pulse GP survey | n/a | 2025 | 712 GPs | about 26% of referral requests rejected, largely advice-and-guidance returns | S |

**Recommended simulator value:** `referral_rejection_rate = 0.05` (sourced-adjacent: it is consistent with the derived FQHC figure of 4.6% for referral problems, and sits inside the published 1.2%-8.5% range for incomplete-form and national e-referral rejections). No GI-specific returned-referral rate for an already-indicated, positive-screen referral was found.

---

## 7. Wrong or unreachable contact details

| Source | URL | Year | Population | Number | Check |
|---|---|---|---|---|---|
| Sea Mar FQHC | https://pmc.ncbi.nlm.nih.gov/articles/PMC8477359/ | 2021 | 711 abnormal FIT | "provider unable to reach patient" in 59 of 402 non-completers = **8.3% of all abnormal FIT** (derived) | P |
| AltaMed FQHC | https://pmc.ncbi.nlm.nih.gov/articles/PMC9716593/ | c. 2022 | 324 abnormal FIT | "patient not found" 8 of 231 (3.5%) = about 2.5% of all; "unable to be notified" under 1% | P |
| Centralised navigation, FQHC | https://www.sciencedirect.com/science/article/pii/S221133552300102X | 2023 | 38 abnormal FIT | 5 (13%) could not be contacted; tiny denominator | S (snippet) |
| Siriwardana et al. | https://pmc.ncbi.nlm.nih.gov/articles/PMC13422818/ | BMJ Open Qual 2026 | 360 outpatients, UK surgery | **20.6%** unreachable after 3 telephone attempts (includes no-answer, so an upper bound on wrong numbers) | P |
| Vital (patient-engagement vendor blog) | https://vital.io/resources/blog/your-patient-data-is-wrong | n/a | 80+ hospitals, self-selected | at least 10% of mobile numbers unreachable; 24% no mobile on file | S (vendor, not peer-reviewed) |
| Mailed-FIT FQHC qualitative study | https://pmc.ncbi.nlm.nih.gov/articles/PMC10538470/ | 2023 | FQHC | inaccurate contact data named as a barrier; no rate | S |
| Pew brief on patient matching | https://www.pew.org/en/research-and-analysis/issue-briefs/2019/09/standardized-demographic-data-improve-patient-matching-in-electronic-health-records | 2019 | US | cross-organisation match rates as low as 50%; not a phone-number error rate | S |
| Clinician.com, admission phone study (1,136 patients; 42% reachable) | https://www.clinician.com/articles/19752-wrong-phone-numbers-threaten-patient-safety | undated | one hospital | could not be identified or verified | S (403, original paper not located) |

No peer-reviewed estimate of the share of EHR phone numbers that are wrong was found.

**Recommended simulator value:** `wrong_contact_rate = 0.08` (sourced-adjacent: derived 8.3% of abnormal-FIT patients that the Sea Mar clinic could not reach; range 0.025 to 0.206). Abu Dhabi note: Emirates ID, phone and email are mandatory fields in the DoH Malaffi minimum dataset (section 8), which argues for the lower half of the range, but no local rate is published.

---

## 8. Abu Dhabi / UAE specifics

### 8a. Eligibility and screening policy

| Item | Source | Finding | Check |
|---|---|---|---|
| Age range and test | UAE National Guideline for Colorectal Cancer Screening and Diagnosis, 3rd ed., 2022 (task force incl. DoH Abu Dhabi), section 3.4 and Standard 2. https://www.isahd.ae/content/docs/Colorectal%20guideline%20Screening%20and%20Diagnosis_UAE%20National%20Guidelines.pdf | Males and females **40-75**; colonoscopy every 10 years, or **FIT every year** (offered if colonoscopy refused). Ages 76-85 individually decided | P |
| IFHAS standard V10 | DoH/SD/ADPHC-CHS/IFHAS/V10/2025, July 2025, Appendix. https://www.doh.gov.ae/-/media/EE758C3B77314BC394F2479AEF803AA2.ashx | FIT package for male and female **40-75 years, annually**; participation KPI target >85% | P |
| ADPHC 2019 revision | https://www.doh.gov.ae/en/news/ADPHC-revises-recommendations-for-early-colorectal-cancer-screening (24 Sep 2019) | Adults **40 and older**: FIT once a year or colonoscopy every 10 years; no positive-result pathway on the page | P |
| Blood test (Shield) | DoH Circular 2025/42, 18 Mar 2025 | Liquid biopsy added to IFHAS for UAE nationals 40+ who are unwilling to have colonoscopy or FIT; "not a replacement" for colonoscopy every 10 years or annual FIT | P |
| 2016 HAAD guidance (FIT every 2 years) | cited in the BMC Cancer 2021 paper | superseded in 2019 by annual FIT | P |

Note: `generate_results.py` previously selected ages **45-75** while the Abu Dhabi documents say **40-75** (no Abu Dhabi source for 45). Fixed at M1 integration (commit "A-fix"): the generator and the Synthea default now use 40-75.

### 8b. Published uptake, positivity and follow-up numbers

| Item | Source | Number | Check |
|---|---|---|---|
| Uptake | Almansoori et al., BMC Cancer 2021;21:897, https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/ (45,147 eligible Emirati nationals, AHS clinics, 2015-2016) | 23.5% screened (FIT/gFOBT in prior 2 years); 54.1% of eligible were under 50 | P |
| Positivity | same | 13.5% positive; 69.6% of positives in women | P |
| Follow-up | same | 436 of 1,429 positives (30.5%) had colonoscopy (49 incomplete); median 72 days; 11 cancers, 133 with polyps | P |
| KPI targets | National Guideline Appendix 2 | uptake >45% acceptable / >65% desirable; **referral to follow-up colonoscopy after positive FIT 90% acceptable / >95% desirable**; **follow-up colonoscopy within 31 working days of referral >90% / >95%**; result to participant within 15 working days (>90%) | P |
| Process clause | National Guideline clause 12.5.2 | positive FIT "urgently referred for follow-up colonoscopy within **15 working days**" (the guideline is internally inconsistent: 15 working days in 12.5.2, 31 working days in Appendix 2, 31 days in Appendix 3) | P |
| IFHAS standard, results and referral | IFHAS V10 sections 4.7.1, 4.9, 4.11, Appendix 9 | providers must run an invitation system including **recall for positive and insufficient results**; ordering physician is responsible for communicating results "especially if they do not show up"; results documented within 10 days of initial visit; abnormal results require the participant physically present; referral to secondary care within **15 working days of releasing results**; KPIs reported within 21 days of quarter end; participants tagged so IFHAS is **trackable through Malaffi** and results tagged in Sahatna; appointment reminders required (4.2.13) | P |
| Positive-blood-test pathway timelines | DoH Circular 2025/42 appendix (Clinical Pathway) | for a positive Shield test: GI consultation **within 7 days**, diagnostic colonoscopy **within 30 days** (marked "to be validated and further outlined by clinical experts") | P |
| Recent uptake or positivity | none found | no published 2017-2026 DoH uptake, positivity or follow-up figure located | gap |

### 8c. Malaffi and FHIR

- Official facts (P): Malaffi is Abu Dhabi's health information exchange run by Abu Dhabi Health Data Services; DoH Circular 2026/10 (19 Jan 2026, https://www.doh.gov.ae/-/media/84A213F71AA545D69E01A23A0A99C598.ashx) makes onboarding mandatory for all licensed facilities, plan within 1 month and live data within 6 months, with licence-renewal consequences. Its attached minimum dataset is expressed as **HL7 message types: ADT, PPR, ORU (lab, radiology, micro, documents, vitals), OMP, RDS, RAS, VXU and SIU (future appointments, appointment mode)**, plus an e-prescription API integration and a cancer staging panel (ORU).
- The DoH HIE standards page (https://www.doh.gov.ae/en/resources/HIE, updated 16 Oct 2026) lists a mandatory minimum dataset including Emirates ID/passport, name, DOB, nationality, **phone number, email**, address (coming soon), allergies, problem list and lab results, and does not mention HL7 or FHIR. The Malaffi policy page (https://malaffi.ae/what-is-malaffi/policies/) lists the DoH HIE policy (April 2020), the privacy standard and ADHICS, and also does not mention FHIR.
- **FHIR R4 support is not confirmed by any official public document.** Some vendor pages claim FHIR integration and one comparison site says DoH XML plus HL7 v2/v3; these are unverified. The official evidence points to **HL7 v2 feeds**. For the simulator, treat Malaffi as an HL7 v2-style event source and the HAPI FHIR server as the project's own internal store; do not claim Malaffi FHIR conformance without the DoH/ADHDS interface specification.

### 8d. Working week

- Federal government: **Monday to Thursday 07:30-15:30, Friday 07:30-12:00; Saturday and Sunday are the weekend**, effective 1 January 2022 (announced Dec 2021). Source: https://u.ae/en/information-and-services/jobs/Sector-of-employment/working-in-uae-government-sector/working-hours-in-the-public-sector (P). The same page states that the Abu Dhabi, Dubai, Ajman, Umm Al Quwain, Ras Al Khaimah and Fujairah governments "adopted a similar work week system"; **Sharjah** works Monday-Thursday with a Friday-Sunday weekend. Also WAM: https://www.wam.ae/en/article/hszrdpi4-uae-government-announces-four-and-half-day-working (page body not retrievable; announcement confirmed through the u.ae page).
- So "Monday to Friday since 2022" is correct with one qualification: Friday is a half day (to 12:00) in government. Private clinics set their own hours (private-sector law: 8 h/day or 48 h/week, https://u.ae/en/information-and-services/jobs/Sector-of-employment/employment-in-the-private-sector/working-hours). The existing pathway SLAs are in "working days"; a calendar of Mon-Fri with Friday as a half day, Sat-Sun off, is the sourced choice.

**Recommended simulator values:** eligible age **40-75**, FIT annually (sourced); working week Mon-Fri with Friday half day, Sat-Sun weekend (sourced); timeliness target for the follow-up colonoscopy **31 working days** (sourced, National Guideline Appendix 2: "within 31 working days of referral"). The pathway measures it as the `confirmatory_completed` milestone in `config/pathways/colorectal_fit.yaml` (ADR-028): the clock runs from the **referral acceptance** by the receiving provider (`handoff_accepted`; it restarts on every later acceptance until the colonoscopy is done) to `complete_confirmatory`, and a case with no formal referral starts it when the case reaches the step that awaits the colonoscopy (ADR-020). It is not measured from the result release. The stricter **15 working days** (guideline clause 12.5.2 and the IFHAS referral KPI "referral to secondary care within 15 working days of releasing results") is the separate `referral_requested` milestone, from the result release (`case_created`) to the referral request. An earlier pathway carried a per-state `complete_confirmatory: 45` working-day SLA, looser than the 31-working-day national KPI; it was aligned to 31 at M1 and has since been replaced by the milestone, so no 45 remains in the pathway.

---

## 9. Harm from delayed colonoscopy after a positive FIT

| Source | URL | Year | Population | Finding | Check |
|---|---|---|---|---|---|
| Corley et al. | https://jamanetwork.com/journals/jama/fullarticle/2620087 | JAMA 2017;317:1631 | 70,124 completers, Kaiser, 2010-2014 | vs 8-30 days: no significant excess to 9 months (any CRC 3.0% at 8-30 d, 4.3% at 7-9 mo, OR 1.30, 0.98-1.71, ns). **10-12 months: any CRC OR 1.48 (1.05-2.08); advanced-stage OR 1.97 (1.14-3.42). >12 months: OR 2.25 (1.89-2.68) and 3.22 (2.44-4.25).** Per extra 30 days OR 1.03 (any) and 1.05 (advanced). Colonoscopy at 1-7 days had higher risk (likely symptom-driven) | P |
| Systematic review (8 studies, 5 FIT) | https://doi.org/10.1016/j.cgh.2020.09.048 (PMID 33010414) | Clin Gastroenterol Hepatol 2021 | published studies to Apr 2020 | beyond 9 months vs within 1 month: CRC adjusted OR 1.75 and 1.48 in the two largest studies; advanced stage 2.79 and 1.55. Conclusion: "should not be delayed beyond 9 months" | P (abstract) |
| San Miguel/Demb et al., US Veterans | https://doi.org/10.1053/j.gastro.2021.01.219 (PMID 33545140) | Gastroenterology 2021 | 204,733 veterans 50-75, abnormal FOBT/FIT 1999-2010 | vs 1-3 months: CRC HR 1.13 (1.00-1.27) at 13-15 mo rising to 1.25-1.28 at 16-24 mo; CRC mortality HR 1.52 at 19-21 mo (the printed CI "1.51-1.99" is inconsistent with the point estimate) and 1.39 at 22-24 mo; late-stage odds rise from about 16 months | P (abstract) |
| PROSPR timeliness benchmark (90 days) | https://doi.org/10.1158/1055-9965.EPI-15-0470 | 2016 | consortium | 90-day target for timely follow-up; quoted by later papers, not in the abstract | S |

Caveats stated by the authors: observational; the 1-7 day group suggests confounding by indication; a JAMA editorial noted no need to rush within months.

**Recommended simulator value:** `delay_harm_threshold_days = 270` (sourced-adjacent: the systematic review's explicit "not beyond 9 months", consistent with Corley's finding that the first statistically significant excess appears at 10-12 months). Use Corley's stage-specific odds ratios as the harm multiplier above that threshold. Keep this as a risk-flag threshold, not a clinical claim: the simulator should report exposure to delay, not diagnose.

---

## 10. Datasets

| Name | URL | Licence / access | Contents | Usable for us? |
|---|---|---|---|---|
| Synthea (colorectal_cancer module) | https://github.com/synthetichealth/synthea/blob/master/src/main/resources/modules/colorectal_cancer.json | Apache-2.0 (checked via GitHub licence API); no registration | Open synthetic patient generator; the module includes a "Screening for occult blood in feces" procedure and positive result (range 150-500 ng/mL, comment cites 100 ng/mL cut-off), routine and diagnostic colonoscopy encounters, adenoma, symptoms, staging, treatment. The diagnostic-colonoscopy wait is a fixed 3-5 days (bowel prep) | Yes for synthetic patients and clinical history (already used). No for the follow-up workflow: it has no notification, ownership, referral, booking, no-show, non-completion or closure, and no realistic delay distribution |
| NHS England Bowel Cancer Screening Annual Report tables 2023-24 | https://www.gov.uk/government/publications/bowel-cancer-screening-annual-report-2023-to-2024 | Open Government Licence v3.0; XLSX, 181 KB; published 25 Feb 2025 | Aggregate invitations, uptake, "further tests needed", diagnostic test offer, attendance within 14 days, uptake | Yes as citable aggregates (1.76% positivity, 76.0% diagnostic uptake, 46.3% attended <=14 d). Not patient-level |
| NHS England Hospital Outpatient Activity | https://digital.nhs.uk/data-and-information/publications/statistical/hospital-outpatient-activity/2024-25 | Official statistics; licence not verified (403 on fetch) | Outpatient attendance and DNA counts and rates by year and specialty | Yes as a general DNA reference once the page is checked |
| NHS Fingertips bowel screening indicators | https://fingertips.phe.org.uk (mirrors list "UK Open Government Licence", CSV) | OGL per third-party mirrors; not verified at source | Area-level bowel screening coverage | Marginal: coverage, not follow-up |
| Public Health Scotland bowel screening statistics | https://publichealthscotland.scot/publications/scottish-bowel-screening-programme-statistics/ | Licence not verified | Biennial aggregate tables (uptake 66%, positivity 2.8%, timeliness) | Yes as citable aggregates |
| RIVM / LECO Dutch evaluation 2018-2021 | link in section 1 | Public PDF; licence not stated | Aggregate positivity, colonoscopy uptake, detection rates | Yes as citable aggregates |
| Kaggle "Medical Appointment No Shows" (110,527 appointments, Vitoria, Brazil, 2016, includes SMS_received) | https://www.kaggle.com/datasets/joniarroba/noshowappointments | Licence not shown on the page; an OpenML mirror lists CC BY-NC-SA 4.0 (third party, unconfirmed) | Appointment-level no-show with SMS flag | Only as an illustrative general no-show source. Not colonoscopy, not screening; confirm licence before redistributing |
| CDC BRFSS | https://www.cdc.gov/brfss/ | US federal public-use annual files; terms not verified here | Survey prevalence of colorectal screening (stool test, colonoscopy) | No for workflows; prevalence only |
| SEER | https://seer.cancer.gov/data/ | Free but requires signing the SEER Research Data Agreement (current: 1975-2023 submission) | Cancer incidence, stage, survival | No: not screening follow-up; useful only for stage distributions |
| HCUP | https://hcup-us.ahrq.gov | Purchase via HCUP Central Distributor; DUA training and signed DUA; data custodian liability | Hospital discharge and ED administrative data | No: paid, restricted, not a screening workflow |
| MIMIC-IV | https://physionet.org/content/mimiciv/ | PhysioNet credentialed access: CITI "Data or Specimens Only Research" training, DUA; 100-patient demo is open | ICU/hospital EHR from Beth Israel | No: DUA forbids sharing, ICU population, no screening workflow |
| eICU-CRD | https://physionet.org/content/eicu-crd/ | Same credentialed route; open demo of about 2,500 stays | Multicentre ICU data | No |
| data.gov / other portals | not searched exhaustively | n/a | n/a | Not assessed |
| Abu Dhabi / UAE open data on screening follow-up | none found | n/a | n/a | No public dataset exists that I could find |

---

## Gaps

What I could not find, with what each gap means for the submission:

1. **Colonoscopy no-show by channel (SMS vs phone vs letter).** No study. The proposal uses a single colonoscopy DNA trial (Lam 2021) plus the Cochrane SMS-equals-phone result. The existing 0.30 / 0.20 / 0.40 stay unsupported if they were meant as something else (for example non-response to first outreach).
2. **Staff acknowledgement delay for a positive result** (`acknowledgement_delay_days`). No public measurement. Policy anchors exist (results to participant within 15 working days; IFHAS results communicated within 10 days of the initial visit) but not the time to first staff acknowledgement. Keep 2 days assumed; it equals the pathway's own SLA, not evidence.
3. **Any 2017-2026 Abu Dhabi uptake, positivity or follow-up figure**, including the new blood-based test. Only the 2015-2016 audit exists, and it mixes FIT and gFOBT and gives no cut-off.
4. **Malaffi FHIR support.** No official statement; official DoH documents show HL7 v2 message types. A request to ddg@doh.gov.ae or hie@doh.gov.ae (contacts printed on the DoH pages) would settle it.
5. **A 2023-2025 meta-analysis of colonoscopy completion after a positive FIT.** Not found; completion distribution is assembled from cohorts (Mohl, Corley, PROSPR, FQHC studies).
6. **Peer-reviewed rate of wrong or unreachable patient phone numbers.** Not found. Only derived non-contact rates from two FQHC audits, a UK QI project and a vendor blog.
7. **Rejection rate for an already-indicated GI referral after a positive screen.** Not found; the proposal borrows from national e-referral data (paediatric Saudi), a colonoscopy central-intake abstract and an FQHC derivation.
8. **Rows I could not verify (marked S):** NHS e-RS "about 2%" rejection, NHS outpatient DNA 5.9%/5.6%, STOP CRC median 72 (49-120), the Springer environmental scan, Latin American positivity by threshold, the Pulse 26% survey, centralised navigation 13% non-contact, and the Kaggle licence. Re-check these before citing.
9. **Source inconsistencies to be aware of:** AltaMed (IQR and the 122 vs 108 completers), Abu Dhabi audit (933 vs about 992 denominator; IQR stated as one number), National Guideline (15 vs 31 working days), VA mortality CI, LA County navigation odds ratio.
10. **Simulator-versus-policy mismatches discovered along the way:** eligible age 45 in `generate_results.py` against 40 in Abu Dhabi policy; `complete_confirmatory` SLA of 45 working days against the 31-working-day national KPI. Both fixed at M1 integration (commit "A-fix: align eligibility age and confirmatory SLA with DoH standard"): the generator and Synthea default are 40-75 and the pathway YAML SLA was set to 31 working days (since A18 it is the `confirmatory_completed` milestone, measured from the referral acceptance: see the last paragraph of section 8).
