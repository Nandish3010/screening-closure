# Go-to-market: a 90-day shadow pilot, the approval path, and the risks

Status: proposal, 25 October 2026 (ticket M7-4). Nothing here has been discussed with any provider, programme, payer or regulator. The buyer below is a **type** of organisation, not a named prospect. The prototype runs on synthetic data only. Figures are from `tools/pilot/cost_model.py` ([business-case.md](business-case.md) sections 8 and 9), and every cost input is assumed. The full active-mode pilot (shadow 30 days, then active 60 days) is in [pilot-plan.md](../pilot-plan.md); this document is the smaller first step that comes before it.

## 1. The proposal in one paragraph

**For a public ambulatory network that delivers IFHAS colorectal screening in Abu Dhabi** (the type of buyer: a group of primary-care clinics that issues FIT and blood-based screening results and refers positives to endoscopy, such as the networks named in the [buyer map](buyer-map.md)), we propose a **90-day shadow run** on **2 to 3 of its clinics**. The engine reads a **periodic HL7 v2 (or CSV) export** of positive results, referrals, appointments and colonoscopy reports. It computes, for every positive, a named owner, the IFHAS clocks and an overdue flag, and shows them to the network's coordinators once a week. **It is read-only: it sends nothing to any patient, writes nothing into the network's systems, and assigns no task to anyone.** Coordinators keep working as they do today. At day 90 the network has a measured baseline of its own follow-up against the IFHAS timelines, a list of positives nobody was tracking, and a judgement from its own coordinators on whether the flags were right. Cost to serve: about **AED 106,000** for 3 clinics (155 positives expected in 90 days at placeholder volumes), offered at cost.

## 2. Why this buyer type first

- The 30.5 percent audit (only 436 of 1,429 positives ever had a colonoscopy) was measured in this kind of network's clinics ([evidence.md](../evidence.md) 8b), and its largest leak was "referred but did not attend", which is the step an owner and a clock act on.
- The network is measured on the IFHAS timelines: referral to secondary care within **15 working days** of releasing the result (IFHAS V10), and for a positive blood-based test a GI consultation within **7 days** and colonoscopy within **30 days** (DoH Circular 2025/42). Both read first-hand ([evidence.md](../evidence.md) 8a, 8b). Today nothing tells it, case by case, which positives are about to miss them.
- A shadow run needs only an export the network already produces for Malaffi-style feeds (Circular 2026/10 lists ADT, ORU and SIU), and no change to anyone's work.
- If it works, the programme owner (ADPHC) is the natural second conversation, with the network's own numbers.

## 3. Scope

| | In | Out |
|---|---|---|
| Pathways | Positive FIT; cervical screening results (implemented and tested on the engine, not clinically reviewed, not simulated); positive blood-based colorectal test | Breast recall (designed only); invitation and recall to screen |
| Data | Results, referrals, appointments, attendance and colonoscopy completion for positives in the window, plus 12 months before for a baseline; staff roster for owner names | Free-text clinical notes; any data about people with a negative result; contact details (not needed when nothing is sent) |
| Actions | Weekly flag report to named coordinators; a readout at day 90 | Any message to a patient; any write-back; any task assignment; any clinical advice |
| Integration | Periodic file drop (daily or weekly) of HL7 v2 messages or CSV, inside the network's environment | Live interface; Malaffi connection |
| Hosting | The network's own approved environment (the Helm chart in `deploy/helm`), or an environment DoH has authorised | Any environment outside the UAE |

## 4. KPIs for the shadow run

All are computed from the export and the event log, not self-reported. The targets are proposals for the network's sponsor and clinical lead to set before day 1 and to write into the letter of intent.

| # | Measure | Definition | Proposed target |
|---|---|---|---|
| S1 | Export completeness | Share of positives in the window that appear in the export with result date and patient identifier, against the laboratory's own count | at least 95% |
| S2 | Ownership visible | Share of positives with an identifiable owner and a running IFHAS clock in the engine | at least 95% |
| S3 | Untracked positives found | Positives with no referral, no appointment and no activity for 30 days that coordinators confirm nobody was working | reported as a count and a rate per 100 positives; no target, this is the finding |
| S4 | Flag agreement | Share of a reviewed sample of overdue flags that a coordinator confirms were right (the case really was late and not already handled) | at least 80% |
| S5 | IFHAS referral timeline, baseline | Share of positive FITs with a referral requested within 15 working days of the result, today, measured per case | reported; the KPI levels in the national guideline are 90% acceptable, 95% desirable |
| S6 | Blood-test timeline, baseline | Share of positive blood tests with GI consultation within 7 days and colonoscopy within 30 days | reported |
| S7 | Staff minutes by role, baseline | Time-and-motion sample of coordinator, owner-clinician and specialist minutes per positive today (K9 in the pilot plan) | reported; no saving is promised |
| S8 | Safety | Messages sent to patients; writes into the network's systems; privacy incidents | **zero, by design and by audit**; any non-zero value stops the run |

## 5. Exit criterion

At day 90 the network's sponsor decides, against the targets agreed before day 1:

- **Proceed to an active pilot proposal** ([pilot-plan.md](../pilot-plan.md), with its gates G0 to G3) if S1 and S2 are at least 95 percent, S4 is at least 80 percent, S8 is zero, and the sponsor judges S3 worth acting on.
- **Stop** otherwise. On stop, or if the network does not proceed within 60 days, all data is deleted or returned within 30 days and a deletion certificate is issued. Either party can stop at any time on written notice.

A stop is a valid result: it tells the network that its follow-up is already tracked, or that its export is not good enough yet, and costs it nothing more.

## 6. Timeline

| Week | What happens | Owner |
|---|---|---|
| -6 to -1 | Letter of intent; data-sharing agreement; information security review against ADHICS; export specification agreed; pathway SLAs confirmed by the network's clinical lead; synthetic test of the export end to end | Network sponsor, data protection officer, us |
| 1 | First export loaded, the baseline 12 months computed; S1 checked | Us |
| 2 to 12 | Weekly flag report; coordinators review a sample (S4); time-and-motion sample (S7) in weeks 2 to 4 | Coordinators, us |
| 13 | Readout: S1 to S8, with intervals; recommendation | Us, network sponsor |

## 7. Procurement and regulatory path

What has to be true before any real record is read, and what it would take to go further. Rows marked to verify rest on secondary sources ([regulatory-mapping.md](../governance/regulatory-mapping.md), [adhics-controls.md](../governance/adhics-controls.md), [dpia.md](../governance/dpia.md)).

| Step | Requirement | Applies to | Source and status | How we meet it |
|---|---|---|---|---|
| 1. Hosting in the UAE | Federal Law No. 2 of 2019 bars storing, processing or transferring health data outside the UAE unless an exception applies (the article is reported as 13; Ministerial Decision 51 of 2021 lists the exceptions) | Shadow and beyond | Secondary; to verify against the instrument | Run inside the network's own environment; support access from inside the UAE; no data leaves the country |
| 2. The cloud rule | ADHICS v0.9 control CM 4.2: no cloud use for health information without DoH authorisation; DoH Circular 147 of 2022 is reported to have urged licensed entities to stop cloud services that store health data, inside or outside the UAE | Any cloud hosting | v0.9 read first-hand; V2 (2024) not retrieved; the circular is secondary, to verify | Shadow run in the network's own approved environment (Helm chart); Azure UAE North only with written DoH authorisation ([deployment.md](../governance/deployment.md)) |
| 3. ADHICS | ADHICS V2 (published May 2024, effective August 2024) is reported to apply to any entity handling health information in Abu Dhabi, third-party systems included | Shadow and beyond | Secondary, to verify; [adhics-controls.md](../governance/adhics-controls.md) maps 68 controls and says 7 are met | The network's information security review; the gaps in the ADHICS table closed or accepted for a read-only run |
| 4. Data protection | Federal Decree-Law 45 of 2021 (PDPL) is reported to exclude health data governed by its own legislation, so Law 2 of 2019 and the DoH standards govern the health record; PDPL may still cover staff accounts and contact data | Shadow and beyond | Secondary, to verify with counsel | [dpia.md](../governance/dpia.md) structured on PDPL; data minimised to positives and dates; pseudonymised identifiers where the export allows |
| 5. The network's own approvals | Sponsor sign-off, data protection officer, information security assessment, a data-sharing and processing agreement | Shadow | Standard practice | Letter of intent ([outreach/letter-of-intent.md](outreach/letter-of-intent.md)), then the agreement |
| 6. DoH | Whether a read-only quality-improvement run under the network's own licence needs DoH notification or approval; whether a publication would need research ethics review | Shadow: to verify. Active mode: clinical safety sign-off, the Responsible AI Standard, and possibly DoH approval | To verify | Ask the network's regulatory affairs lead first; ask DoH through the ecosystem route ([buyer-map.md](buyer-map.md) section 3) |
| 7. Malaffi | Not needed for the shadow run; a programme deployment needs the operator's agreement and interface specification | Programme scale | [malaffi-adapter-plan.md](../governance/malaffi-adapter-plan.md) | HL7 v2 adapter; requested only after a pilot readout |
| 8. Procurement | Shadow run at cost under the network's direct purchase rules; a programme licence through the programme owner's route, likely the Abu Dhabi Government Procurement Gate | Shadow; licence | Thresholds and route to verify | Supplier registration when a programme conversation starts |
| 9. People | A clinical lead who reviews the pathway (H1) and a native Arabic reviewer (H2) before any active mode; a named clinical safety officer | Active mode | Not yet in place | Recruited through the outreach in [outreach/](outreach/README.md) |

## 8. Risk register

Likelihood and impact are our judgement on a 1 (low) to 3 (high) scale. The owner is who would act; "us" is the operator.

| # | Risk | L | I | Owner | Mitigation | Early sign |
|---|---|---|---|---|---|---|
| R1 | No organisation agrees to a shadow run | 3 | 3 | Us | Ask for the smallest thing (read-only, at cost, stop any time); go through mentors and the ecosystem route as well as cold outreach; accept a single clinic | No reply after two follow-ups to each contact type |
| R2 | Hosting not approved for health data | 2 | 3 | Network IT, us | Run in the network's environment with the Helm chart; no cloud dependency | Security review asks for a cloud architecture |
| R3 | The export is incomplete or late | 2 | 2 | Network IT | S1 checked in week 1; shadow mode makes gaps visible before any action | S1 below 95% in week 1 |
| R4 | Flags mostly wrong (alert fatigue) | 2 | 3 | Us, coordinators | Weekly tuning with coordinators; S4 target agreed in advance; stop if not met | S4 below 60% at week 4 |
| R5 | Nothing untracked is found (the network already follows up well) | 2 | 2 | Network sponsor | Report it as a finding; the timeline baselines (S5, S6) still have value; move to a network with a known gap | S3 near zero by week 6 |
| R6 | A privacy or security incident | 1 | 3 | Us, network DPO | Data minimised, no contact details, in-country, role-based access, audit log, PHI log scan; any incident stops the run (S8) | Any access outside the named roles |
| R7 | Clinician minutes rise in active mode | 3 | 2 | Network clinical lead | K9 stop rule in the pilot plan; route escalations to nurse navigators before active mode | Simulation already predicts it; measured in S7 |
| R8 | Avoided cost lands on the payer, not the buyer | 3 | 2 | Us | Do not sell on avoided cost; price on safety, timelines and capacity; bring the payer in as co-funder after the readout | Buyer asks for a cash return |
| R9 | The buyer's EMR vendor offers a worklist or care-gap module | 2 | 2 | Us | Position on the guarantees (no closure without evidence, cross-provider clock, audit trail) and on IFHAS timeline reporting; integrate rather than replace | The network says its EMR "already does this" |
| R10 | Regulatory change (ADHICS V2 cloud clauses, Responsible AI Standard revision due October 2026) | 2 | 2 | Us | Re-read both before the security review; design already in-country | New circular |
| R11 | No clinician or Arabic reviewer available (H1, H2) | 2 | 3 | Us | Ask for reviewers in every outreach; active mode cannot start without them | No reviewer by the shadow readout |
| R12 | Operator capacity: a very small team | 3 | 2 | Us | Shadow scope is small (one export, weekly report); no second pilot before a readout | Support load above the 0.3 FTE assumed |
| R13 | Synthetic results do not transfer | 3 | 2 | Us | Claim nothing from the simulation in the pilot; the shadow run measures the baseline | Real baseline far from the simulated one (expected) |
| R14 | Price rejected at programme scale | 2 | 2 | Us | Three price points (per screened member, per closed case, licence); cost per actionable case about AED 70 at programme scale gives room ([business-case.md](business-case.md) section 9) | Budget owner cannot name a budget line |

## 9. After the shadow run

1. **Active pilot** at the same clinics ([pilot-plan.md](../pilot-plan.md)): owners work from the worklist, messages after owner approval, clinician-signed closures; priced at cost.
2. **Programme conversation** with ADPHC: FIT and blood-based positives across two networks, then cervical screening (implemented on the engine, to be reviewed) and breast recall (designed only) once reviewed.
3. **Programme licence** ([business-case.md](business-case.md) section 9): about AED 2 million a year for a four-pathway scope (two implemented, two designed only; illustrative), against an operator cost to serve of about AED 1.7 million at list-price hosting.
