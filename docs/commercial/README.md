# Commercial front page

Read this page first; the detail is in [business-case.md](business-case.md). **No buyer exists, no price has been tested, nobody has been contacted, and there is no letter of intent.** Every figure below carries its grade: **sourced** (read from a published page), **assumed** (our input, not a quote), or **simulated** (synthetic, from the model).

## Stated up front: staff cost per case rises

The system frees **coordinator hours**; it does not cut the cost of a case. At wage ratios from published UAE salary pages (a clinician 4 times and a specialist 10 times a coordinator; sourced-adjacent, not an official pay scale; [evidence-wages.md](../evidence-wages.md)), wage-weighted staff time per case goes **up**, by about 11% at programme level in the literature-anchored scenario, because more patients reach colonoscopy and clinicians and specialists spend more time. The 40% labour-reduction target is **not met** (19.7% programme-level, 36.0% where the system reaches a case; simulated). The pilot would replace the assumed minutes with a time-and-motion study (KPI K9, [pilot-plan.md](../pilot-plan.md)). The commercial claim is therefore capacity plus outcomes, not savings.

## What is sourced

| Item | Figure | Source |
|---|---|---|
| The clock the network is measured on | Referral to secondary care within **15 working days** of releasing a positive FIT result; for a positive blood-based test, GI consultation within 7 days and colonoscopy within 30 days | DoH IFHAS V10 (July 2025) and DoH Circular 2025/42, read first-hand ([evidence.md](../evidence.md) 8a, 8b). Whether DoH enforces them through a performance indicator is to verify (business case section 2). |
| The local gap | Only 30.5% of 1,429 positive results in Abu Dhabi ambulatory clinics had a follow-up colonoscopy | BMC Cancer 2021, 2015 to 2016 data |
| The offer | A 90-day **read-only shadow run** at two or three providers, about **AED 106,000** to serve, offered at cost. Needs only a periodic export; sends nothing to a patient. | [go-to-market.md](go-to-market.md); the AED figure is arithmetic on assumed inputs, not a quote |

## A cost script anyone can rerun

`python3 tools/pilot/cost_model.py` prints every cost from [`tools/pilot/inputs.yaml`](../../tools/pilot/inputs.yaml), where each input carries its status (assumed, sourced-adjacent or sourced) and a basis. The AED 106,000 above is its shadow-run total.

**Change one input and rerun.** Copy `inputs.yaml`, set `shadow_support_fte` (support staff on the shadow run) from 0.3 to 0.5, and run `python3 tools/pilot/cost_model.py --inputs your-copy.yaml`. The "SHADOW PILOT" block moves from a total of AED 105,828 to AED 120,622 and from AED 681 to AED 776 per case. Nothing else moves. Any input can be changed the same way, and `--pricing` does the same for the licence table.

## What is assumed (a hypothesis, not a finding)

| Item | Figure | Why it is only an assumption |
|---|---|---|
| Licence | About **AED 2.0 million** a year for a four-pathway programme licence (two pathways implemented, bowel and cervical; two designed only) | No price has been tested with anyone. |
| Cost to serve | About AED 1.71 million a year at about 24,600 actionable cases | Hosting at Azure UAE North list prices; every other input assumed. |
| Margin and payback | 14% margin; 21-month operator payback on the integration and pathway work | Follows from the two rows above. |
| Avoided cost | Avoided first-year treatment cost covers 69% of the licence (FIT pathway) | Simulated, with observational odds ratios and an unvalidated 270-day timing. Avoided cost alone does not pay the fee. |
| Outcomes | About 42 advanced-stage cancers and about 31 colorectal cancer deaths within five years avoided a year in Abu Dhabi | Simulated, from one outcomes model and the same roughly 17,700 FIT-positive results a year. The two figures are different measures (stage at diagnosis; death within five years), which is why the second is smaller. The 24,600 cases above are the four-pathway licence volume and set the cost to serve. |

## Next steps (for the owner; none has happened)

The [buyer map](buyer-map.md) is desk research from public sources. The ready-to-send [outreach kit](outreach/README.md) holds the pilot brief, a letter of intent and the emails; [contacts.md](outreach/contacts.md) lists who to approach. Nothing has been sent.
