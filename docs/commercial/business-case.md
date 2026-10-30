# Business case without a named buyer

Status: draft, 22 October 2026 (ticket E17, milestone M6); pricing, hosting cost and payback added 25 October 2026 (ticket M7-4, section 9), with the buyer map, go-to-market and outreach kit moved to their own files ([buyer-map.md](buyer-map.md), [go-to-market.md](go-to-market.md), [outreach/](outreach/README.md)). This is the strongest case we can make honestly **before any buyer has seen it**. Nothing here has been discussed with any buyer, payer, regulator or provider, and none is a prospect list. The prototype runs on synthetic data only. Every number is tagged with its grade: **sourced** (read from the cited page), **sourced-adjacent** (a published figure for a related setting, year or population), **derived** (our arithmetic on cited figures) or **assumed** (our choice). Anything we could not confirm is marked **to verify**. All model numbers come from `python3 tools/pilot/cost_model.py --commercial`, whose full output is pasted in section 8; the inputs and their grades are in `tools/pilot/inputs.yaml` (sections `market`, `outcome`, `capacity`, `benchmark`). Currency is AED; US dollars convert at the dirham peg of 3.6725 (to verify against the Central Bank of the UAE).

## 0. Executive summary (one page)

**The offer.** Give every positive screening result a named owner, a clock and a clinician-signed closure, starting with a read-only shadow run on a provider's own export. The case rests on **capacity first, outcomes second, price last**.

**What is being sold: a programme licence across pathways (a hypothesis).** The unit is the screening programme, not the clinic. Today only the colorectal FIT pathway is built and simulated (`config/pathways/colorectal_fit.yaml` is the only pathway in the repository). The licence would cover positives from three sources: FIT; blood-based colorectal screening, where a positive still needs a colonoscopy to confirm (section 2), so the same follow-up gap applies; and the breast and cervical recall pathways, which should fit the same engine of owner, clock and closure. No breast or cervical pathway has been written or simulated, so that part is an untested expectation. The target buyer is Abu Dhabi's public health programme (ADPHC, which runs IFHAS). That is a hypothesis about who would pay, not a relationship: no buyer exists, no letter of intent has been signed, and no price has been tested.

**The three numbers a buyer cares about.**

1. **Size: about 17,700 FIT-positive cases a year in Abu Dhabi** (range 8,200 to 59,700); about 44,800 in the UAE (20,800 to 151,200). Derived from 1.26 million residents aged 40 to 75 (Statistics Centre Abu Dhabi, 2024, [citypopulation.de reproduction](https://www.citypopulation.de/en/uae/abudhabi/admin/)), times 20 percent with a FIT result in a year (assumed; evidence: 23.5 percent of eligible Emiratis screened in 2015-2016, [BMC Cancer 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/); under 20 percent, [The National, 2024](https://www.thenationalnews.com/news/uae/2024/12/15/abu-dhabi-colorectal-cancer-test/)), times 7 percent positive (Gulf programmes 6.5 to 6.8 percent, section 1). The gap it addresses is measured locally: only **30.5 percent** of 1,429 positive results in Abu Dhabi ambulatory clinics had a follow-up colonoscopy (same BMC Cancer paper, 2021; 2015-2016 data).
2. **Return: AED 78 to 177 of first-year treatment cost avoided per positive case** in the literature-anchored simulation (AED 158 to 357 in the upper-range scenario), against an illustrative per-case price of AED 90. In cancers, that is **about 42 advanced-stage cancers avoided per year in Abu Dhabi** (95% interval 29 to 84; 2.39 per 1,000 positives), from the health outcomes model ([outcomes.md](../results/outcomes.md), section 4.4), which carries each simulated patient's days to colonoscopy through Corley's stage odds ratios. This is a synthetic simulation with observational odds ratios and an unvalidated 270-day timing; it is assumption-based and not a forecast. Method: simulated drop in cases with no colonoscopy by day 270 (46.5 to 31.9 percent, [results.md headline](../results/results.md)), times the excess advanced-stage risk of late colonoscopy ([Corley, JAMA 2017](https://jamanetwork.com/journals/jama/fullarticle/2620087), odds ratios 1.97 and 3.22), times a stage cost gap from the only Gulf study found ([Saudi Arabia, 2020 prices, stage I USD 45,317 to stage IV above USD 71,374](https://pmc.ncbi.nlm.nih.gov/articles/PMC10630777/)), with an assumed 0.6 stage-pair factor. The 270-day shares rest on the simulator's timing between 180 and 360 days, which is not validated (after the B21 timing fix only two timing claims pass held-out validation, and neither covers days 180 to 360; [validation.md](../results/validation.md)), so treat this return as indicative. **Honest reading: outcomes alone roughly cover a price of AED 90 only if the delay harm is at the higher odds ratios or the upper-range reach; at the lower case they fall short (0.87 times the price).** Avoided treatment cost alone does not cover the fee. The case for paying rests on outcomes (cancers found earlier) and on programme operations (every positive has an owner and a clock), not on cost savings. Do not sell on avoided cost alone.
3. **Capacity: 1.7 coordinator FTE freed per 10,000 positives a year** (literature-anchored, programme level; 3.0 in reach; 3.2 upper range), from 46.6 to 29.8 coordinator minutes per case (`effort.scenarios.literature_anchored.all.arms.{baseline,orchestrated}.minutes_per_case.coordinator.mean`). The same run consumes **0.4 owner-clinician and 0.1 specialist FTE more**, so all-roles FTE freed is 1.1. This is capacity, not cash: wage-weighted staff time per case rises 10.9 percent ([pilot-plan.md section 8](../pilot-plan.md)).

4. **Price and cost: a programme licence of about AED 2.0 million a year for four pathways, against an operator cost to serve of about AED 1.71 million** (24,596 actionable cases a year at central volumes, AED 69.6 per case; hosting at Azure UAE North list prices, every other input assumed; section 9). That is a 14 percent margin and a 21-month operator payback on AED 1.17 million of integration and pathway work. Avoided treatment cost from the FIT pathway covers 69 percent of the licence (157 percent at the beyond-12-month odds ratios), and the net cost to the buyer is about AED 14,600 per advanced-stage cancer avoided at the base case. The buyer pays for safety and outcomes, not cash: about 4,367 positives a year that the simulation says would otherwise be lost without anyone told.

**What a buyer can do on Monday.** A shadow run at 2 to 3 providers costs about **AED 106,000** to serve (155 cases in 90 days; every cost input assumed, none quoted) and needs only a periodic export. It answers flag agreement and untracked cases, and measures the provider's own baseline against the IFHAS timelines; it cannot answer closure KPIs, which need active mode. The proposal, KPIs, exit criterion, approval path and risk register are in [go-to-market.md](go-to-market.md).

**What we do not claim.** No buyer exists. No price has been tested. The simulator is synthetic, the minutes are not measured in Abu Dhabi, and the odds ratios are observational. No Jawda indicator we could read currently measures colorectal follow-up (section 2), so a regulatory pull is to verify, not assumed.

## 1. Market sizing

### 1.1 Abu Dhabi and the UAE

| Item | Figure | Source (year) | Grade |
|---|---|---|---|
| Abu Dhabi residents, census | 3,789,860 (first release); 3,847,585 (revised) | [SCAD via Abu Dhabi Media Office, 11 June 2024](https://www.mediaoffice.abudhabi/en/government-affairs/statistics-centre-abu-dhabi-reports-results-of-abu-dhabi-census-2023/); revised figure from the SCAD update | sourced |
| Abu Dhabi residents, 2024 | 4,135,985; 54 percent aged 25-44; median age 33 | [Department of Government Enablement, 30 June 2025](https://dge.gov.ae/en/news/2025-scad-population-update) | sourced |
| Abu Dhabi by age, 2024 | 40-49: 811,235; 50-59: 333,260; 60-69: 101,990; 70-79: 29,995 | [citypopulation.de](https://www.citypopulation.de/en/uae/abudhabi/admin/), source note "Statistic Centre Abu Dhabi"; SCAD portal not readable | sourced-adjacent (third-party copy) |
| Abu Dhabi aged 40-75 | **1,264,000** (30.6 percent of residents) | Sum of the bands above plus 60 percent of the 70-79 band for ages 70-74 (split assumed) | derived |
| UAE residents | 10,678,556 (2023, preliminary); 11,027,129 (2024) | [FCSC series via GLMM](https://gulfmigration.grc.net/?p=10258); [UN WPP 2024 via populationpyramid.net](https://www.populationpyramid.net/united-arab-emirates/2024/) | sourced-adjacent |
| UAE aged 40-75 | **3,200,000** | The FCSC age table returned HTTP 403, so this scales the Abu Dhabi share to the UAE total and rounds down; to verify at [FCSC open data](https://opendata.fcsc.gov.ae/@federal-competitiveness-and-statistics-center/uae-population-estimates-by-gender-agegroup-year) | assumed |

**Who is eligible.** The Department of Health and ADPHC recommend FIT every year or colonoscopy every ten years from age 40 ([DoH news, ADPHC revises recommendations, 2019](https://www.doh.gov.ae/en/news/ADPHC-revises-recommendations-for-early-colorectal-cancer-screening); the page could not be fetched, so this is from a search summary and is to verify); ADPHC awareness material and the IFHAS colorectal package describe 40 to 75 ([IFHAS, ADPHC](https://www.adphc.gov.ae/en/Public-Health-Programs/IFHAS) lists Thiqa holders aged 18 to 75; the 40-to-75 colorectal range is from a search summary of ADPHC and Mediclinic material and the upper limit of 75 is **to verify** on doh.gov.ae). The blood-based test (Shield) is offered from 50 without family history, as an alternative for people who decline FIT or colonoscopy, not a replacement ([The National, 15 December 2024](https://www.thenationalnews.com/news/uae/2024/12/15/abu-dhabi-colorectal-cancer-test/); [Guardant announcement, 30 January 2025](https://www.businesswire.com/news/home/20250130054040/en/Guardant-Healths-Shield-Test-Selected-by-Abu-Dhabi-Department-of-Health-for-Blood-Based-Colorectal-Cancer-Screening-Program), first-year target about 10,000 people).

**Uptake.** 23.5 percent of 45,147 eligible Emirati nationals screened by FIT or gFOBT in 2015-2016 ([BMC Cancer 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8343888/)); regular screening "below 20 per cent" in the general population ([Prof Humaid Al Shamsi in The National, 2024](https://www.thenationalnews.com/news/uae/2024/12/15/abu-dhabi-colorectal-cancer-test/)); 88 percent of UAE residents over 40 never screened, survey sponsored by Sheikh Shakhbout Medical City, sample size not stated ([Khaleej Times, 9 March 2022](https://www.khaleejtimes.com/lifestyle/health/uae-nearly-90-per-cent-of-residents-above-the-age-of-40-have-never-undergone-a-colorectal-screening?amp=1)). The model uses 20 percent central, 10 percent low, 35 percent high (assumed; the high end is a programme ambition, not an observation).

**Burden and stage.** 67 percent of new colorectal cases in Abu Dhabi in 2023 were diagnosed late; colon cancer is 8 percent of cases; officials aim to move early-stage detection from 33 to 50 percent within two years ([The National, 15 December 2024](https://www.thenationalnews.com/news/uae/2024/12/15/abu-dhabi-colorectal-cancer-test/); original DoH figure **to verify**).

**Positivity.** Qatar 6.8 percent (758 of 11,130; [PMC9822526](https://pmc.ncbi.nlm.nih.gov/articles/PMC9822526/)); Saudi Ministry of Health pilot 6.6 percent of 47,158 tests, Al-Kharj 6.3 percent ([PMC11161890](https://pmc.ncbi.nlm.nih.gov/articles/PMC11161890/)); Bahrain 716 of 11,000, 6.5 percent ([MDPI 2024](https://www.mdpi.com/2624-5647/6/3/41)); Abu Dhabi ambulatory clinics 13.5 percent in 2015-2016. The model takes 7 percent central, 6.5 to 13.5 percent as the range; the pilot plan's 7 percent is consistent.

**Expected FIT-positive cases a year** (model output, section 8):

| | Low | Central | High |
|---|---|---|---|
| Abu Dhabi FIT results | 126,400 | 252,800 | 442,400 |
| Abu Dhabi FIT-positive cases | 8,216 | **17,696** | 59,724 |
| UAE FIT results | 320,000 | 640,000 | 1,120,000 |
| UAE FIT-positive cases | 20,800 | **44,800** | 151,200 |

What follows the positive result is where the volume leaks: 30.5 percent had a follow-up colonoscopy in the Abu Dhabi audit ([evidence.md section 2](../evidence.md)), against 56.1 percent in Qatar's interim programme data and 20.4 percent in the Saudi pilot. The simulator's calibrated baseline reaches 51.0 percent colonoscopy within 180 days, which is above the local audit (30.5 percent ever, 2015-2016); evidence.md notes the local figure sits below the baseline. The simulated gain is therefore not proven for Abu Dhabi, and a real baseline measured in the shadow run would replace it.

### 1.2 GCC extension

| Country | Residents | Programme (finding) | Source (year) | Central FIT-positives a year (model) |
|---|---|---|---|---|
| Saudi Arabia | 32.2 million (census 2022); aged 45-75 about 5.5 million | Ministry of Health FIT for ages 45-75 at primary care; 2017-2022 pilot in 417 centres, 47,158 tests, 20.4 percent of positives had colonoscopy. A 2024 review says there is **no national programme**; the sources conflict, so the status is **to verify** | [MOH awareness page](https://www.moh.gov.sa/en/awarenessplateform/ChronicDisease/Pages/ColorectalCancer.aspx); [PMC11161890](https://pmc.ncbi.nlm.nih.gov/articles/PMC11161890/); [GASTAT census 2022 via GLMM](https://gulfmigration.grc.net/saudi-arabia-total-population-by-age-group-2010-2022-data-adjusted-by-census-2022/) | 38,500 (at 10 percent participation, assumed) |
| Qatar | 3,143,491 (2024) | National programme since 2016 run by the Primary Health Care Corporation: ages 50-74, FIT every two years (the Ministry page says yearly; to verify); 56.1 percent of positives had colonoscopy | [PMC9822526](https://pmc.ncbi.nlm.nih.gov/articles/PMC9822526/); [PHCC](https://www.phcc.gov.qa/en/Clinics-And-Services/Screening-And-Detection/Bowel-Cancer-Screening); [GLMM](https://gulfmigration.grc.net/?p=11454) | together with Kuwait, Oman and Bahrain: 15,744 |
| Kuwait | 4,987,826 (December 2024, PACI via Gulf News) | A pilot for ages 45-75 launched in 2015; no national FIT programme found; to verify | [PMC8265403](https://pmc.ncbi.nlm.nih.gov/articles/PMC8265403); [Gulf News](https://gulfnews.com/world/gulf/kuwait/kuwaits-population-hits-49-million-with-expats-making-up-more-than-two-thirds-1.500122608) | (included above) |
| Oman | 5,268,072 (December 2024, NCSI via Muscat Daily) | No population programme (2024 review); national guideline issued April 2023, FIT content to verify | [PMC9298662](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9298662/); [Ministry guideline](https://www.moh.gov.om/media/eeenmvjz/national-guidelines-for-colorectal-cancer-crc-screening-diagnosis-management-and-follow-up.pdf); [Muscat Daily](https://www.muscatdaily.com/2025/01/26/oman-population-increases-2-to-5-268mn-in-2024/) | (included above; no programme, so treat as zero until one exists) |
| Bahrain | 1,594,654 (December 2024) | King Hamad University Hospital FIT campaign from 2019 for ages 45-75 (11,000 enrolled, 716 positive); Royal Medical Services campaign March 2024; primary-care roll-out under way; a national programme is **to verify** | [MDPI](https://www.mdpi.com/2624-5647/6/3/41); [EMPHNET/BNA](https://epinews.emphnet.net/en/news/bahrain-royal-medical-services-launches-national-colorectal-cancer-screening-campaign); [GLMM](https://gulfmigration.grc.net/bahrain-population-estimates-by-nationality-bahraini-non-bahraini-1991-dec-2024/) | (included above) |

For the four smaller states no age table was read, so the eligible population is total residents times an assumed 15 percent (12 to 18 percent; Saudi implies 17 percent). The GCC figures are order-of-magnitude only; Oman should be read as zero today, so the 15,744 overstates the programme-backed volume.

### 1.3 Obtainable market

At the illustrative fees, **the revenue pool is small**: option B (AED 90 per positive case) gives AED 1.6 million a year in Abu Dhabi and AED 4.0 million in the UAE at the central volume; option A (AED 6 per member screened) gives AED 1.5 million and AED 3.8 million. The cost-to-serve model puts Abu Dhabi central (252,800 FIT results, 17,696 cases) at about AED 1.19 million a year, or AED 67 per case (all inputs assumed, `cost_model.py` default output), so a fee of AED 90 leaves a margin of roughly AED 0.4 million. That is a viable reference-programme business and a thin standalone one; the argument for building it is the pathway and audit trail it makes reusable for other screening programmes (a mammography and a cervical pathway have DoH standards), not the single pool. Section 9 prices the multi-pathway programme licence that answers this, with hosting at list price.

## 2. Buyer map

The full map, with each buyer's role, payment flows, procurement route, Malaffi position and what it values, all sourced and with every unverified item marked, is in [buyer-map.md](buyer-map.md) (M7-4; it replaces the table that was here). In short: DoH regulates and sets the standards (IFHAS V10, the breast and cervical standards V2, and Circular 2025/42, which gives a positive blood-based test a 7-day GI consultation and a 30-day colonoscopy); ADPHC runs IFHAS through more than 150 facilities and is the hypothesised licence buyer; Daman administers Thiqa and carries the avoided treatment cost; PureHealth's SEHA network and M42 deliver the screening and are the first shadow-pilot buyers; private groups follow. No organisation has been contacted.

Order of approach, by what we can evidence: a provider group with a coordinator capacity problem (shadow run), then ADPHC as programme owner, then a payer. We have no evidence yet that any of the three would pay.

## 3. Comparables and pricing

Public prices for this product category are scarce; most vendors do not publish them. What we found, with its weakness stated.

| Benchmark | Figure | Source (year) | Weakness |
|---|---|---|---|
| NHS cancer information system, East Kent and Kent and Medway | GBP 2 million over 5 years (up to 2 more), about GBP 400,000 a year by our arithmetic; scope: suspected-cancer referrals, treatment tracking, patient portal, screening and surveillance | [HTN, 26 February 2025](https://htn.co.uk/2025/02/26/nhs-kent-and-medway-opens-procurement-for-2m-cancer-information-system/) | A procurement notice, not an award; population served not stated; far broader scope |
| Care-gap-closure platforms, 50,000 lives | USD 48,000 to 300,000 a year (AED 176,000 to 1.1 million), implementation USD 10,000 to 100,000; that is AED 3.5 to 22 per member a year | [Vendor-hosted comparison, 26 March 2026](https://ustechautomations.com/resources/blog/care-gap-closure-automation-comparison) | Written on one vendor's own blog; unverified; US health-plan context |
| Human patient navigation | Median USD 150 per person navigated (interquartile 66 to 338; AED 551, range 242 to 1,241); median USD 663 per additional person screened (202 to 1,711; AED 2,435); 2022 dollars | [Community Guide systematic economic review](https://thecommunityguide.org/findings/cancer-screening-patient-navigation-services-to-increase-colorectal-cancer-screening.html) | Mostly US programmes for screening uptake, not positive-FIT follow-up |
| Referral-management software | Priced per provider per month, per location, per referral, or as platform fee plus implementation; most vendors do not publish prices | [Vendor blog, 18 September 2026](https://clinekthealth.com/blog/referral-management-software-pricing) | Vendor-authored; no figures |

No UAE or GCC contract value for care-navigation, referral-management or care-gap software was found. **To verify** through tender portals and the buyers themselves.

**Where our options sit** (all fees illustrative and assumed; not an offer):

| Option | Price | Against the benchmarks | Comment |
|---|---|---|---|
| Shadow run | Flat fee at cost recovery, about AED 106,000 for 3 providers and 90 days (model, assumed inputs) | Small against any benchmark above | Buys evidence from the buyer's own records; answers flag agreement, not closure |
| A: per member screened | AED 6 a year | At the low end of the implied AED 3.5 to 22 per member | Predictable; pays for coverage, not for follow-up |
| B: per positive case tracked | AED 90 | 16 percent of the median human navigation cost per person (AED 551); 0.86 to 1.94 times the avoided treatment cost in section 4 | Aligned to the work that matters; revenue falls if positivity falls |
| C: hybrid | Small platform fee plus AED 40 to 60 per positive case, and an additional fee per documented closure with evidence | Not modelled | Our recommendation for the active phase: it ties price to the thing the buyer cares about without making the buyer pay twice for the same case |

The cost model puts our cost to serve at AED 481.8 per case at 10,000 members, 136.7 at 50,000 and 70.5 at 200,000 (assumed inputs). A fee of AED 90 loses money below roughly 100,000 to 110,000 screened members (cost per case falls through AED 90 in that range in the model) and earns a margin above it. That is why the pilot is priced at cost and the steady-state fee is argued at programme scale.

## 4. Outcome-based return

### 4.1 Treatment cost by stage

No UAE stage-specific cost study was found. The only Gulf figures are from one Saudi tertiary hospital.

| Study | Setting and year | Stage I | Stage IV | In AED | Notes |
|---|---|---|---|---|---|
| [Colorectal cancer resource use and costs, Saudi Arabia](https://pmc.ncbi.nlm.nih.gov/articles/PMC10630777/) (2023) | 326 patients, Riyadh, enrolled 2017-2020, payer view, direct medical cost in the year after diagnosis, 2020 prices | USD 45,317 | above USD 71,374 (a lower bound) | 166,400 and 262,100 | Stage II and III only in a figure; overall mean USD 58,384 (text) or 58,968 (table) |
| [Azzani et al., Malaysia](https://pmc.ncbi.nlm.nih.gov/articles/PMC6419868/) (2019) | One tertiary hospital, provider cost, first year, 2012 prices | MYR 13,486 (abstract says 13,672) | MYR 27,972 | 16,000 and 33,100 at the paper's MYR 3.10 per USD, not inflated | Understates in current prices |
| [SEER-Medicare, ISPOR 2010 abstract](https://www.ispor.org/heor-resources/presentations-database/presentation/ispor-13th-annual-international-meeting/differences-in-colorectal-cancer-treatment-costs-by-treatment-phase-cancer-site-and-stage-at-diagnosis-evidence-from-linked-seer-medicare-data) | Colon cancer, ages 65 and over, diagnosed 1996-2002, initial phase, 2006 USD | stage 0: USD 17,278 | USD 40,501 | 63,500 and 148,700 | Excess over matched controls; US prices |
| [Long-term costs in Spain](https://pmc.ncbi.nlm.nih.gov/articles/PMC4756512/) | Barcelona, 16-year horizon, 2005 euros | EUR 31,757 | EUR 28,061 (stage III EUR 47,681) | not converted | Stage IV is lower because survival is short; shows the gap is not monotonic over a lifetime |

The model uses the **Saudi gap** (stage I to IV, AED 95,694) as the central case and the **Malaysian gap** (AED 17,161) as the low case, and multiplies by an assumed **stage-pair factor of 0.6** (0.4 to 1.0), because Corley's advanced stage is III and IV and the cost studies give I and IV.

### 4.2 Stage shift from timely colonoscopy

[Corley et al., JAMA 2017](https://jamanetwork.com/journals/jama/fullarticle/2620087) (70,124 completers, Kaiser, 2010-2014; [evidence.md section 9](../evidence.md)): relative to colonoscopy at 8 to 30 days, no significant excess to 9 months; at **10 to 12 months** any cancer odds ratio 1.48 and advanced stage 1.97; **beyond 12 months** 2.25 and 3.22. The absolute advanced-stage risk is not in evidence.md. A JAMA commentary reports 19 per 1,000 at 10 months and 31 per 1,000 beyond 12 months; inverting the odds ratios gives an on-time risk of 0.0097 to 0.0098, which the model uses (0.0098; derived; **to verify** against the paper's table). The simulator's `delay_harm_threshold_days` is 270 and its shares of cases with no colonoscopy by then are 46.5 percent at baseline, 31.9 percent literature-anchored and 17.1 percent upper range ([results.md headline](../results/results.md)).

### 4.3 Result per 1,000 positive FITs

| Scenario and odds ratio | Cases moved to colonoscopy by day 270 | Advanced-stage cases avoided | AED avoided, Malaysian gap | AED avoided, Saudi gap | AED per case, Saudi gap | Against AED 90 |
|---|---|---|---|---|---|---|
| Literature-anchored, 10-12 month ORs | 146.2 | 1.36 | 14,039 | 78,284 | 78 | 0.87 times |
| Literature-anchored, beyond-12-month ORs | 146.2 | 3.08 | 31,745 | 177,017 | 177 | 1.97 times |
| Upper range, 10-12 month ORs | 294.7 | 2.75 | 28,299 | 157,799 | 158 | 1.75 times |
| Upper range, beyond-12-month ORs | 294.7 | 6.21 | 63,990 | 356,817 | 357 | 3.96 times |

Range for the headline row (literature-anchored, 10 to 12 month ORs, Saudi gap) over the pair factor: **AED 52,189 to 130,473 per 1,000**, AED 78,284 central. The fee at which the avoided cost equals the price is AED 78 per case on the headline row. At Abu Dhabi's central volume of 17,696 positives, avoided first-year treatment cost is AED 1.4 to 3.1 million a year on the two literature-anchored rows, against our assumed cost to serve of AED 1.19 million.

**Labelled assumptions.** (1) The shares are synthetic simulator output, not observed; (2) Corley's odds ratios are observational and subject to confounding by indication, and apply to a different population; (3) cases that never complete are treated as if they completed late, which is the harm model's simplification; (4) only first-year direct treatment cost is counted, not survival, deaths, productivity or later years, so the figure understates the societal value and says nothing about lives; (5) the stage-pair factor and the use of one Saudi hospital as the central cost are our choices; (6) no discounting or inflation; (7) the 270-day shares rest on the simulator's timing between 180 and 360 days, which is not validated (after the B21 timing fix only two timing claims pass held-out validation, and neither covers days 180 to 360; [validation.md](../results/validation.md)), so the table is indicative; the completion ratio at 180 days passed. Read the table as an order of magnitude whose sign is robust and whose size is not.

**Reading against price.** Avoided cost accrues to the payer or the treating system years after the fee is paid, and the simulator shows a 15-point drop in no-colonoscopy by day 270 only under the literature-anchored assumptions. It supports a per-case price of tens of dirhams, not hundreds, at the lower case; it supports AED 90 only if the odds ratios are at the higher end or the upper-range reach holds. The case a buyer will actually buy on is in section 5.

### 4.4 Advanced cancers avoided per year in Abu Dhabi

This turns section 4.3 into a count of cancers. It uses only figures already in this document: the advanced-stage cases avoided per 1,000 positive FITs (section 4.3, literature-anchored rows) and Abu Dhabi's central positive count of 17,696 a year (section 1 and the summary above: 1,264,000 residents aged 40 to 75, times 20 percent with a FIT result, times 7 percent positive).

| Row (literature-anchored) | Per 1,000 positives | Arithmetic for 17,696 positives | Advanced-stage cancers avoided a year |
|---|---|---|---|
| 10 to 12 month odds ratios | 1.36 | 17,696 / 1,000 x 1.36 = 17.696 x 1.36 | about 24.1 |
| Beyond-12-month odds ratios | 3.08 | 17,696 / 1,000 x 3.08 = 17.696 x 3.08 | about 54.5 |

Sections 4.3 and 4.4's odds-ratio arithmetic prices avoided treatment cost; it is not the count of cancers quoted elsewhere in this document. **The count is the health outcomes model's** ([outcomes.md](../results/outcomes.md), `make evaluate-outcomes`, literature-anchored headline): **2.39 advanced-stage (III-IV) cancers avoided per 1,000 positives, about 42 a year in Abu Dhabi (95% PSA interval 29 to 84)**, with colorectal cancer deaths within 5 years avoided at 1.76 per 1,000 (1.16 to 2.90), about 31 a year, and 389 QALYs a year at about AED 23,626 per QALY. Method: each simulated patient's days from positive FIT to colonoscopy are turned into cancer and stage with Corley 2017's delay odds ratios, the baseline arm is subtracted from the orchestrated arm, and the parameters are drawn from their ranges (2,000 draws) for the interval. The upper-range scenario would give 4.81 per 1,000 (about 85 a year), a mechanism ceiling and not a forecast. The positive count (17,696) is itself an estimate (8,216 to 59,724) and its range is not carried into the interval.

**Caveats.** This is a synthetic simulation, not an observation. The odds ratios are observational (Corley, Kaiser, 2010 to 2014) and apply to a different population. The 270-day timing is not validated (section 0). The positive count is itself an assumption-based estimate. The result is an illustration of scale, not a forecast, and no one should quote it as a number of lives or cancers saved.

## 5. Capacity value

Minutes per case by role are in `docs/results/results.json` under `effort.scenarios.<scenario>.<all|in_reach>.arms.<arm>.minutes_per_case.<role>.mean` ([results.md, Human effort](../results/results.md)). Literature-anchored, programme level: coordinator 46.6 to 29.8 minutes (-36.1 percent); owner clinician 4.5 to 8.7 (+94 percent); specialist 6.1 to 7.4 (+22.6 percent). With 1,700 productive hours per FTE (assumed; 1,500 to 1,900):

| Per 10,000 positive FITs a year | Coordinator FTE | Owner-clinician FTE | Specialist FTE | All roles |
|---|---|---|---|---|
| Literature-anchored, programme level | +1.65 (1.47 to 1.87) | -0.41 | -0.13 | +1.10 |
| Literature-anchored, cases in reach | +3.01 (2.69 to 3.41) | -0.75 | -0.25 | +2.01 |
| Upper range | +3.21 (2.87 to 3.63) | -0.71 | -0.26 | +2.24 |

At Abu Dhabi's central 17,696 positives the literature-anchored row scales to about 2.9 coordinator FTE freed and 0.7 owner-clinician plus 0.2 specialist FTE consumed. **This is capacity, not cash.** The baseline workload is 4.6 coordinator FTE per 10,000 positives, so the freed share is real against a coordinator team. But clinician and specialist time rises, wage-weighted staff time per case goes up 10.9 percent at the sourced-adjacent wage ratios of 4 and 10 ([pilot-plan.md section 8](../pilot-plan.md); [evidence-wages.md](../evidence-wages.md)), and the base simulation has no queue. Capacity is modelled separately (B19, [results.md, Under finite capacity](../results/results.md)): at base capacity the 180-day gain survives (+15.7 points), the 90-day gain is about zero (+0.7 points), and median time to colonoscopy among completers rises by 15.0 days; at 70 percent of base slot supply the 180-day gain is +5.2 points. More people complete, but the queue is endoscopy supply, which is a buyer decision. Staff shifts are not modelled beyond a daily minutes budget. The honest pitch is "grow screening without growing coordinators, and watch clinician minutes (K9)", not "save labour cost". The simulator's 40 percent labour-reduction target is **not met in realistic scenarios; the upper bound comes close**: 36.0 percent in reach and 19.7 percent at programme level (literature-anchored), and 40.0 percent only in the upper range, a mechanism ceiling and not a forecast (95% interval 39.0 to 40.9).

## 6. Go-to-market

**Wedge.** One programme (colorectal FIT, with the blood-test positives as an add-on if ADPHC agrees), one provider group, a **90-day shadow run** on a periodic flat-file or HL7 v2 export of FIT results, referrals and colonoscopy records. Nothing is sent or assigned. About AED 106,000 to serve (assumed). Output: owners, clocks and overdue flags that coordinators review weekly, a count of positives nobody was tracking, and measured minutes (K9). Success is defined before it starts, from the pilot-plan KPIs, and written into the letter of intent below.

**Step two.** Active mode at the same providers (the full 90-day pilot: AED 577,624 to serve in the model, 3,718 per case at pilot scale, assumed), with the stop rule. Price at cost, converting to option C at programme scale.

**Expansion.** (a) More providers in the same programme, which improves cost per case (AED 481.8 to 70.5 as volume goes from 10,000 to 200,000 members); (b) IFHAS-wide colorectal follow-up under ADPHC; (c) the other screening pathways: blood-based colorectal screening positives and the breast and cervical recall pathways (breast and cervical have DoH standards; none is built yet, and the repository has only the colorectal FIT pathway), which is where the programme licence in section 0 would extend; (d) a payer member-level view; (e) the GCC programmes in section 1.2 only after a reference in Abu Dhabi, starting where a programme exists (Qatar, Saudi Arabia, Bahrain).

**Risks.**

| Risk | Effect | Mitigation |
|---|---|---|
| No payer or programme owner pays | No revenue | Lead with capacity; shadow run at cost; test willingness to pay early with the letter of intent |
| Outcome value below price | Price pressure | Do not sell on avoided cost; use option C and report measured closure |
| Clinician minutes rise | Resistance, lost capacity gain | Measure K9; route fix is planned; stop rule |
| Synthetic evidence does not transfer | Claims fail in pilot | Say so; the shadow run measures before the buyer commits |
| No Jawda or incentive pull | Slow decisions | Verify other Jawda sets; seek an ADPHC sponsor |
| Prototype-to-production gap | Pilot delay | [pilot-hardening checklist](../governance/pilot-hardening.md); pilot starts after gate G0 |
| Small revenue pool | Not a standalone business | Position as a reusable pathway platform; avoid fixed-cost growth before a second programme |
| Data sharing and residency | Blocks the shadow run | In-country hosting; data-sharing agreement; security assessment; Malaffi optional ([malaffi-adapter-plan.md](../governance/malaffi-adapter-plan.md)) |

### Letter of intent

The letter-of-intent template is now [outreach/letter-of-intent.md](outreach/letter-of-intent.md): non-binding, shadow mode only (read-only, no patient contact, no write-back), with the success measures S1 to S8 of [go-to-market.md](go-to-market.md). It replaces the broader draft that was here. The risk register is in [go-to-market.md](go-to-market.md) section 8.

## 7. Open points and what stayed unverifiable

1. UAE population aged 40-75 (FCSC table blocked; assumed 3.2 million). SCAD portal table for Abu Dhabi bands (read via a third-party copy).
2. The DoH upper age limit of 75 for colorectal screening, the 2019 ADPHC recommendation text (page not fetchable), and the current IFHAS colorectal package by sex.
3. Any Jawda or DoH indicator for screening follow-up (the primary care guidance for Q3 2026 has none); payer contract incentives.
4. A DoH innovation sandbox (not found); government procurement route for pilots.
5. Saudi national programme status (sources conflict); Kuwait, Oman and Bahrain programme status and age tables for all four smaller states.
6. UAE or Gulf stage-specific CRC treatment cost beyond one Saudi hospital; Saudi stage II and III values; UAE price points for care-navigation software (none found).
7. Corley absolute advanced-stage risk (derived from a commentary) and the 67 percent late-stage figure's original DoH source.
8. The dirham peg citation; Daman and Thiqa member counts for 2024.
9. Every cost-to-serve and price input in the model is assumed; participation, the stage-pair factor and productive hours are our choices. All effort and 270-day shares are synthetic.

## 8. Model output (deterministic)

Reproduce with `python3 tools/pilot/cost_model.py --commercial`. The default run (`python3 tools/pilot/cost_model.py`) is unchanged and is the output quoted in [pilot-plan.md section 8](../pilot-plan.md). Tests: `.venv/bin/pytest tools/pilot/tests -q -p no:cacheprovider`.

```
Currency: AED. Inputs file: inputs.yaml.
COMMERCIAL CASE EXTENSION (ticket E17; illustrative, not a quote, forecast or clinical claim)
Reads the market, outcome, capacity and benchmark sections of the inputs file, and the simulated 270-day shares and
minutes by role from results.json (synthetic). Statuses are graded per input: assumed, sourced-adjacent, sourced.

COMMERCIAL INPUTS
input                                        value    low   high  status            unit
-----------------------------------------  -------  -----  -----  ----------------  ---------------------------------------------------------------------------------------------------------------------------------
abu_dhabi_population_40_to_75              1264000      -      -  sourced-adjacent  residents aged 40 to 75, Abu Dhabi emirate, 2024
uae_population_40_to_75                    3200000      -      -  assumed           residents aged 40 to 75, UAE, about 2024
fit_participation_annual                       0.2    0.1   0.35  assumed           share of the eligible population with a FIT result in a year
fit_positivity_market                         0.07  0.065  0.135  sourced-adjacent  share of FIT results that are positive
saudi_population_45_to_75                  5500000      -      -  sourced-adjacent  residents aged 45 to 75, Saudi Arabia, 2022
qatar_population_total                     3143491      -      -  sourced-adjacent  residents, Qatar, 2024
kuwait_population_total                    4987826      -      -  sourced-adjacent  residents, Kuwait, December 2024
oman_population_total                      5268072      -      -  sourced-adjacent  residents, Oman, December 2024
bahrain_population_total                   1594654      -      -  sourced-adjacent  residents, Bahrain, December 2024
gcc_eligible_share_of_population              0.15   0.12   0.18  assumed           share of total residents in the screening age range, four smaller GCC states
gcc_fit_participation_annual                   0.1   0.05    0.2  assumed           share of the eligible population with a FIT result in a year, GCC outside Abu Dhabi
advanced_stage_risk_on_time_derived         0.0098      -      -  sourced-adjacent  risk, advanced stage, colonoscopy at 8-30 days
usd_aed_peg                                 3.6725      -      -  sourced           AED per USD
crc_cost_stage1_usd_saudi                    45317      -      -  sourced-adjacent  USD per patient, first year after diagnosis, stage I, 2020 prices
crc_cost_stage4_usd_saudi                    71374      -      -  sourced-adjacent  USD per patient, first year after diagnosis, stage IV, 2020 prices
crc_cost_stage1_myr_malaysia               13485.9      -      -  sourced-adjacent  MYR per patient, first year, stage I, 2012 prices
crc_cost_stage4_myr_malaysia                 27972      -      -  sourced-adjacent  MYR per patient, first year, stage IV, 2012 prices
myr_per_usd_malaysia                           3.1      -      -  sourced-adjacent  MYR per USD
stage_pair_factor                              0.6    0.4    1.0  assumed           share of the stage I to stage IV cost gap that applies to one case moved from advanced stage (III or IV) to early stage (I or II)
productive_hours_per_fte_year                 1700   1500   1900  assumed           productive hours per full-time coordinator per year
navigation_cost_per_person_navigated_usd       150     66    338  sourced-adjacent  USD per person navigated (median and interquartile range), 2022 prices
navigation_cost_per_additional_person_usd      663    202   1711  sourced-adjacent  USD per additional person screened (median and interquartile range), 2022 prices

MARKET: expected FIT results and FIT-positive cases a year (eligible x participation x positivity)
Abu Dhabi and UAE use ages 40-75 and the same participation and positivity; Saudi uses ages 45-75 and the GCC participation;
Qatar, Kuwait, Oman and Bahrain use total residents x the assumed eligible share and the GCC participation.
per year                                             low    central       high
---------------------------------------------  ---------  ---------  ---------
participation (share with a FIT)                     10%        20%        35%
positivity                                          6.5%       7.0%      13.5%
Abu Dhabi: FIT results                           126,400    252,800    442,400
Abu Dhabi: FIT-positive cases                      8,216     17,696     59,724
UAE: FIT results                                 320,000    640,000  1,120,000
UAE: FIT-positive cases                           20,800     44,800    151,200
Saudi Arabia: FIT results                        275,000    550,000  1,100,000
Saudi Arabia: FIT-positive cases                  17,875     38,500    148,500
Qatar+Kuwait+Oman+Bahrain: eligible            1,799,285  2,249,106  2,698,928
Qatar+Kuwait+Oman+Bahrain: FIT-positive cases      5,848     15,744     72,871
Revenue pool at the illustrative fees, central case: option B (AED 90 per positive case) Abu Dhabi 1,592,640, UAE 4,032,000; option A (AED 6 per member screened) Abu Dhabi 1,516,800, UAE 3,840,000.

OUTCOME VALUE: treatment cost avoided per 1,000 positive FITs (ILLUSTRATIVE, SYNTHETIC SHARES x PUBLISHED ORs x COST GAP)
Shares with no colonoscopy by day 270 (synthetic): baseline 46.5%, literature-anchored 31.9%, upper-range 17.1%.
On-time advanced-stage risk 0.0098 (derived from Corley 2017 absolute risks). Stage I to IV cost gap per patient, first year:
  Saudi study (USD, 2020 prices x 3.6725): AED 95,694; Malaysian study (MYR, 2012, not inflated): AED 17,161.
Pair factor (share of that gap that one advanced-to-early shift saves; assumed): 0.6, sensitivity 0.4 to 1.
per 1,000 positive FITs               moved to colonoscopy  late-stage avoided  AED avoided, Malaysian gap  AED avoided, Saudi gap  AED per case, Saudi gap  avoided / option B AED 90
------------------------------------  --------------------  ------------------  --------------------------  ----------------------  -----------------------  -------------------------
literature-anchored, 10-12 month ORs                 146.2                1.36                      14,039                  78,284                       78                      0.87x
literature-anchored, >12 month ORs                   146.2                3.08                      31,745                 177,017                      177                      1.97x
upper-range, 10-12 month ORs                         294.7                2.75                      28,299                 157,799                      158                      1.75x
upper-range, >12 month ORs                           294.7                6.21                      63,990                 356,817                      357                      3.96x
Sensitivity of the headline row (literature-anchored, 10-12 month ORs, Saudi gap) to the pair factor, AED avoided per 1,000: 0.4: 52,189, 0.6: 78,284, 1: 130,473.
Break-even for the headline row: the option B fee equals the cost avoided when it is AED 78 per case (the illustrative fee is 1.15 times that).
Direct treatment cost in the first year only; excludes survival, deaths, productivity and later years. Cases that never
complete are treated as if they completed late. Observational ORs, confounding by indication.

CAPACITY VALUE: coordinator and clinician FTE freed per 10,000 positive FITs a year (synthetic minutes; capacity, not cash)
Minutes by role from results.json effort (keys effort.scenarios.<scenario>.<all|in_reach>.arms.<arm>.minutes_per_case.<role>.mean);
FTE = cases x minutes saved / 60 / productive hours (1700 per FTE a year, assumed; range 1500 to 1900). Positive = freed.
scenario (vs baseline of same cases)  coordinator FTE  owner clinician FTE  specialist FTE  all roles FTE  coordinator FTE range (hours)
------------------------------------  ---------------  -------------------  --------------  -------------  -----------------------------
literature anchored, programme level            +1.65                -0.41           -0.13          +1.10                 +1.47 to +1.87
literature anchored, in reach                   +3.01                -0.75           -0.25          +2.01                 +2.69 to +3.41
upper range (every case reached)                +3.21                -0.71           -0.26          +2.24                 +2.87 to +3.63
Baseline workload for scale: 4.6 coordinator FTE, 0.4 owner-clinician FTE and 0.6 specialist FTE for 10,000 positives.
Coordinator time is freed; owner-clinician and specialist time rises, so the all-roles figure is much smaller than the
coordinator figure and clinician time is the scarcer resource. No queueing or shift pattern is modelled.

PRICE BENCHMARKS (human patient navigation, for positioning the per-case price)
benchmark (2022 USD x peg)                                         low  median   high
-----------------------------------------------------------------  ---  ------  -----
navigation cost per person navigated, AED                          242     551  1,241
navigation cost per additional person screened, AED                742   2,435  6,284
option B fee AED 90 as a share of the median per person navigated    -   16.3%      -
These are US-led human-navigator programmes for screening, not positive-FIT software contracts; they bound what a buyer
already pays per navigated person, they are not a price list for this product.
```

## 9. Pricing, total cost of ownership and payback (ticket M7-4)

This section prices the programme licence of section 0 and puts the hosting on public list prices. The method: Azure UAE North pay-as-you-go prices read from the public [Azure Retail Prices API](https://prices.azure.com/api/retail/prices) on 25 October 2026 (filter `armRegionName eq 'uaenorth'`; human pages for [Container Apps](https://azure.microsoft.com/pricing/details/container-apps/), [PostgreSQL Flexible Server](https://azure.microsoft.com/pricing/details/postgresql/flexible-server/), [Azure Monitor](https://azure.microsoft.com/pricing/details/monitor/), [Key Vault](https://azure.microsoft.com/pricing/details/key-vault/), [Azure DNS](https://azure.microsoft.com/pricing/details/dns/), [Container Registry](https://azure.microsoft.com/pricing/details/container-registry/), [Application Gateway](https://azure.microsoft.com/pricing/details/application-gateway/) and [Private Link](https://azure.microsoft.com/pricing/details/private-link/)), times the sizes in `deploy/bicep` ([deployment.md](../governance/deployment.md)); every other input is assumed and graded in `tools/pilot/inputs.yaml`. Two things the API showed matter for the plan: Azure Health Data Services (the managed FHIR service) returned no UAE North price, so a self-hosted FHIR server is costed, and no Private Link endpoint meter came back for UAE North, so that one price is the global rate and to verify. Hosting on Azure is still subject to DoH authorisation (ADHICS CM 4.2, [deployment.md](../governance/deployment.md)); in a provider's own data centre the hosting line would be the provider's cost instead.

Everything between the markers below is generated by `python3 tools/pilot/cost_model.py --pricing --write`, and a test (`tools/pilot/tests/test_cost_model.py`, part of `make check`) fails if it is stale.

<!-- BEGIN GENERATED: python3 tools/pilot/cost_model.py --pricing --write (do not edit by hand) -->

Generated from `tools/pilot/inputs.yaml` (sections `azure`, `pathways`, `pricing`, `operator`, `buyer`, plus the earlier sections) and `results.json` (synthetic). AED unless stated; USD at the peg of 3.6725. Illustrative: not a quote, an offer or a forecast.

**9.1 Hosting on Azure UAE North at list price** (production, the `deploy/bicep` defaults; pay-as-you-go, no reservation, no free grant)

| item | sizing | USD a month | AED a year |
|---|---|---:|---:|
| Container Apps: api, scheduler, watchdog | 2.75 vCPU, 5.5 GiB always on | 217 | 9,555 |
| Container Apps: FHIR server (HAPI) | 2 vCPU, 4 GiB | 158 | 6,949 |
| Container Apps: collector, Prometheus, Grafana | 0.5 vCPU, 1 GiB | 39 | 1,737 |
| Container Apps environment | 730 h | 104 | 4,600 |
| PostgreSQL: case database compute | 4 vCores x 2 (zone-redundant HA) | 634 | 27,925 |
| PostgreSQL: case database storage | 128 GB x 2 | 35 | 1,557 |
| PostgreSQL: backup above the included amount | 64 GB, LRS, in country | 7 | 296 |
| PostgreSQL: FHIR database compute | 2 vCores x 2 | 317 | 13,962 |
| PostgreSQL: FHIR database storage | 64 GB x 2 | 18 | 778 |
| Log Analytics: ingestion and retention | 20 GB a month, 2 months billed retention | 71 | 3,132 |
| Key Vault: operations | 500,000 a month | 2 | 87 |
| Private endpoint and private DNS zones | 1 endpoint, 2 zones | 8 | 366 |
| Container Registry, Standard | 1 registry | 20 | 894 |
| Application Gateway WAF v2 (TLS gateway) | 1 gateway, 2 capacity units | 374 | 16,504 |
| **Production total** |  | **2,005** | **88,343** |
| Test environment (40% of production, assumed) |  |  | 35,337 |
| **Hosting a year** |  |  | **123,680** |

The earlier assumed hosting input (AED 9,000 a month, AED 108,000 a year, used by the pilot model) is 0.87 times this list-price build-up, so the pilot model's hosting is close to list price. The pricing below uses the build-up. Database compute with a standby is the largest line; a reservation would cut it (not applied, no reservation price read).

**9.2 Volumes a year in Abu Dhabi** (FIT from section 1; the other pathways are assumed or a programme target, and none is built or simulated)

| pathway | screened, central | actionable cases, low | central | high |
|---|---:|---:|---:|---:|
| FIT positives | 252,800 | 8,216 | 17,696 | 59,724 |
| Blood-based screening positives | 10,000 | 500 | 1,200 | 3,000 |
| Breast screening recalls | 40,000 | 1,250 | 3,200 | 8,400 |
| Cervical screening abnormal results | 50,000 | 900 | 2,500 | 6,400 |
| **All four** | **352,800** | 10,866 | **24,596** | 77,524 |

**9.3 Operator cost to serve a year** (central volumes; every input assumed except the Azure unit prices)

| line | FIT only | programme, four pathways |
|---|---:|---:|
| hosting (Azure UAE North list price, production and test) | 130,758 | 133,518 |
| third-party licences | 24,000 | 24,000 |
| engineering and support | 529,200 | 904,200 |
| clinical safety officer | 180,000 | 180,000 |
| ADHICS audit, penetration test, data protection review | 80,000 | 80,000 |
| integration, one feed per provider group (amortised) | 240,000 | 240,000 |
| pathway builds beyond FIT (amortised) | 0 | 150,000 |
| **Total cost to serve** | **1,183,958** | **1,711,718** |
| cost per actionable case | 66.9 | 69.6 |
| one-off investment (integrations, pathway builds) | 720,000 | 1,170,000 |

**9.4 Price points and operator margin** (illustrative fees: AED 6 per screened member, AED 90 per tracked case, AED 140 per case closed with evidence (simulated closed share 67.8%), and a programme licence of AED 2,000,000 a year for 4 pathways; margin = (revenue - cost to serve) / revenue)

| price point | FIT only: revenue | margin | four pathways: revenue | margin |
|---|---:|---:|---:|---:|
| A: per screened member | 1,516,800 | +22% | 2,116,800 | +19% |
| B1: per tracked case | 1,592,640 | +26% | 2,213,640 | +23% |
| B2: per closed case | 1,680,757 | +30% | 2,336,116 | +27% |
| L: programme licence | not offered | - | 2,000,000 | +14% |

**9.5 Operator break-even and payback** (programme, four pathways, central)

| measure | value |
|---|---:|
| break-even fee per tracked case | AED 69.6 |
| break-even fee per closed case | AED 102.6 |
| break-even programme licence | AED 1,711,718 a year |
| operator one-off investment | AED 1,170,000 |
| licence less cash running cost (before amortisation) | AED 678,282 a year |
| operator payback on the one-off investment, licence | 21 months |
| operator payback with the buyer's implementation fee | 16 months |

**9.6 What the buyer gets a year from the FIT pathway** (the only pathway simulated; synthetic shares, observational odds ratios, assumed wages; literature-anchored, programme level)

| item | AED or count a year |
|---|---:|
| avoided first-year treatment cost, 10-12 month ORs (cash, years later) | 1,385,307 |
| avoided first-year treatment cost, beyond-12-month ORs | 3,132,485 |
| coordinator capacity freed (2.9 FTE at AED 105,000) | 306,123 |
| owner-clinician time consumed (0.7 FTE at 4 times) | -305,818 |
| specialist time consumed (0.2 FTE at 10 times) | -249,072 |
| **net labour, wage-weighted** | **-248,767** |
| advanced-stage cancers avoided (count; outcomes model, central and 95% interval) | 42 (29 to 84) |
| positives no longer lost without anyone told (count; silent loss 45.3% to 20.6%) | 4,367 |
| IFHAS 15-working-day referral and 31-working-day colonoscopy clocks, measured per case | not monetised |

**9.7 Buyer break-even and payback** (fee less avoided treatment cost, against what it buys; the cash return accrues to the treating system years after the fee)

| fee | avoided cost covers | net cost a year | net cost per advanced cancer avoided | net cost per positive no longer silently lost | payback of the AED 250,000 implementation fee |
|---|---:|---:|---:|---:|---:|
| B1 per tracked case, FIT only (AED 1,592,640) | 87% | 207,333 | 4,910 | 47 | none (no positive annual cash) |
| same, beyond-12-month ORs | 197% | -1,539,845 | saves money | saves money | 2 months |
| L programme licence, four pathways (AED 2,000,000), FIT value only | 69% | 614,693 | 14,556 | 141 | none (no positive annual cash) |
| same, beyond-12-month ORs | 157% | -1,132,485 | saves money | saves money | 3 months |

**9.8 Sensitivity** (one input at a time; programme licence, four pathways; buyer column uses the FIT value only, 10-12 month ORs unless the row says otherwise)

| case | operator cost a year | licence margin | cost per actionable case | FIT avoided cost covers licence | buyer net cost per advanced cancer avoided |
|---|---:|---:|---:|---:|---:|
| **base case** | 1,711,718 | +14% | 69.6 | 69% | 14,556 |
| volumes low (all pathways) | 1,456,626 | +27% | 134.1 | 32% | 69,201 |
| volumes high (all pathways) | 2,122,289 | -6% | 27.4 | 234% | saves money |
| licence price -25% | 1,711,718 | -14% | 69.6 | 92% | 2,716 |
| licence price +25% | 1,711,718 | +32% | 69.6 | 55% | 26,395 |
| hosting -50% | 1,649,878 | +18% | 67.1 | 69% | 14,556 |
| hosting +100% | 1,835,398 | +8% | 74.6 | 69% | 14,556 |
| support FTE per 50,000 screened halved | 1,447,118 | +28% | 58.8 | 69% | 14,556 |
| support FTE per 50,000 screened doubled | 2,240,918 | -12% | 91.1 | 69% | 14,556 |
| provider groups 3 | 1,591,718 | +20% | 64.7 | 69% | 14,556 |
| provider groups 12 | 1,951,718 | +2% | 79.4 | 69% | 14,556 |
| stage-pair factor low | 1,711,718 | +14% | 69.6 | 46% | 25,490 |
| stage-pair factor high | 1,711,718 | +14% | 69.6 | 115% | saves money |
| beyond-12-month ORs | 1,711,718 | +14% | 69.6 | 157% | saves money |

**9.9 Net cost per death avoided and per QALY** (FIT pathway, literature-anchored; deaths and QALYs from [outcomes.md](../results/outcomes.md), M7-3: colorectal cancer deaths within 5 years avoided and QALYs discounted at 3%, from Corley 2017's delay odds ratios, Saudi and SEER stage survival and WHO UAE life expectancy; for 17,696 positives a year that is 31.1 deaths (20.5 to 51.3) and 389 QALYs (245 to 678), 95% PSA intervals. Net cost is the fee less the avoided treatment cost of 9.7, 10-12 month ORs; the second pair of columns adds the extra colonoscopies the outcomes model counts (2,507 a year at an assumed AED 4,000 each), which this cost model otherwise leaves to the payer. Ranges divide by the PSA bounds. Illustrative.)

| fee | net cost a year | per death avoided | per QALY | per death avoided, with colonoscopies | per QALY, with colonoscopies |
|---|---:|---:|---:|---:|---:|
| B1 per tracked case, FIT only (AED 1,592,640) | 207,333 | 6,662 (4,044 to 10,122) | 533 (306 to 845) | 328,823 (199,632 to 499,600) | 26,297 (15,100 to 41,697) |
| L programme licence, four pathways (AED 2,000,000), FIT value only | 614,693 | 19,751 (11,991 to 30,008) | 1,580 (907 to 2,505) | 341,912 (207,578 to 519,486) | 27,343 (15,701 to 43,357) |

**Reading.** At the central inputs the programme licence costs the operator AED 1,711,718 a year to serve (69.6 per actionable case across 24,596 cases), so a licence of AED 2,000,000 carries a 14% margin. Avoided treatment cost from the FIT pathway alone covers 69% of the licence (157% at the beyond-12-month odds ratios), and wage-weighted labour is a net cost of AED 248,767 a year in the current routing, so neither pays the fee. The buyer pays for the remainder in safety and outcomes: about 4,367 positives a year no longer lost without anyone told and 42 (29 to 84) advanced-stage cancers avoided (outcomes model, synthetic, assumption-based), plus a per-case record against the IFHAS timelines, and in health terms about 31 colorectal cancer deaths avoided within 5 years for each year of positives (20 to 51), at a net cost of about AED 1,580 per QALY on the licence before the extra colonoscopies and AED 27,343 with them (9.9). The other three pathways add volume and cost here but no modelled value, because none is built or simulated.

<!-- END GENERATED: pricing -->

**How to say it (regulatory pull first).** The case for paying is a regulatory and risk case, not a savings case. Abu Dhabi's colorectal screening standard already sets clocks that a network is measured on: referral to secondary care within 15 working days of releasing a result (DoH IFHAS V10, July 2025, read first-hand, [evidence.md](../evidence.md) section 8a), and, for a positive Shield blood test, a GI consultation within 7 days and a colonoscopy within 30 days (DoH Circular 2025/42, marked there "to be validated and further outlined by clinical experts"). Today nothing tells a network, case by case, which positives are about to miss them; the product gives every positive an owner, a clock against those timelines, an audit trail and a clinician-signed closure, so a missed hand-off becomes a flagged one, and the local audit that motivates it found only 30.5 percent of positive results reaching a colonoscopy (BMC Cancer 2021). What is not claimed: the repository holds no source showing that a regulator or payer currently penalises a missed positive (no Jawda or incentive pull was found, section 2 and section 7), and no medicolegal or liability argument is sourced, so none is made here. Savings alone do not close the case, and we say so: avoided first-year treatment cost covers 69 percent of the licence.

**The economics, plainly.** The operator can serve a four-pathway programme in Abu Dhabi for roughly AED 1.7 million a year at list-price hosting and assumed staffing, so a licence around AED 2 million is a viable business, and it is larger than the FIT-only pool of section 1.3. The buyer does not get that money back in cash: avoided first-year treatment cost covers about two thirds of it at the lower odds ratios, and the labour line is a cost until escalations are routed to nurse navigators. What the buyer pays for is a programme where every positive has an owner and a clock: about 4,400 positives a year that the simulation says would otherwise be lost without anyone told, an advanced cancer avoided for roughly AED 25,000 of net cost at the base case, and a per-case record against the IFHAS timelines. In health terms the FIT pathway alone is about 31 colorectal cancer deaths within 5 years avoided for each year of positives (20 to 51) and 389 discounted QALYs, which puts the licence at roughly AED 1,600 net per QALY before the extra colonoscopies and about AED 27,000 with them (9.9; synthetic, observational odds ratios, survival not from the UAE, no clinician review).

**One slide: pilot economics (shadow run).** Scale: 3 providers, 90 days, about 155 actionable cases ([pilot-plan.md](../pilot-plan.md), section 3). Cost to serve: about AED 106,000 in total (105,828; about AED 681 per case; every input assumed, none quoted; the full active pilot is 577,624, or 3,718 per case). It needs only a periodic export of the provider's own FIT results, referrals and colonoscopy records; nothing is sent in the first 30 days. What it answers: flag agreement, untracked cases and the provider's own baseline against the IFHAS timelines; it does not answer closure KPIs, which need active mode. Exit criterion at day 90 ([go-to-market.md](go-to-market.md), section 5): proceed to an active pilot proposal if export completeness (S1) and visible ownership (S2) are at least 95 percent, flag agreement (S4) is at least 80 percent, the safety count (S8: messages sent, writes into the network's systems, privacy incidents) is zero and the sponsor judges S3 worth acting on; otherwise stop, with data deleted or returned within 30 days. A stop is a valid result. All of it is a model on assumed inputs, on synthetic data.

**Note on QALYs and deaths avoided.** Dated 29 October 2026 (M7-4), filled in by M7-3 the same day: the deaths and QALYs now come from [`docs/results/outcomes.md`](../results/outcomes.md) (`make evaluate-outcomes`), and section 9.9 divides the same net cost as 9.7 (fee less avoided first-year treatment cost) by them, with and without the extra colonoscopies the outcomes model counts. The outcomes model's own cost per QALY (about AED 23,626 for AED 90 per case, colonoscopies included) uses its own avoided-cost estimate, which counts more advanced cancers avoided than section 4.3 (it applies the beyond-12-month odds ratios to cases with no colonoscopy within a year); 9.9 keeps this document's 10-12 month avoided-cost figure, so it is the more conservative of the two. The advanced-cancer counts in sections 1, 9.6 and 9.8 and the net cost per advanced cancer come from the outcomes model (42 a year, 29 to 84).
