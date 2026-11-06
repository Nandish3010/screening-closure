# Decision memo: a read-only shadow run on the 15-working-day referral clock

For a Department of Health reader. 7 November 2026. One page. Nothing here has been discussed with DoH or with any provider; the prototype uses synthetic data only. Full offer: [pilot-offer.md](pilot-offer.md).

## The decision asked

Whether a provider network may run a **90-day, read-only shadow pilot** that computes, for each positive screening result, a named owner and the clocks in the DoH IFHAS standard, without contacting any patient or changing any record. Three specific answers would unblock it:

1. Does a read-only quality-improvement run, under the provider's own licence and in its own environment, need DoH notification or approval? (Open: [go-to-market.md](go-to-market.md) section 7, step 6.)
2. Is the **referral within 15 working days of releasing a result** (IFHAS V10, July 2025) reported as a performance indicator, and is it counted to the referral request or to the receiving provider's acceptance? We count the request.
3. Who at DoH, or at the provider, would act as clinical reviewer of the pathway clocks and wording? None has reviewed them.

## Why now

- Only 30.5% of 1,429 positive results in Abu Dhabi ambulatory clinics had a follow-up colonoscopy (BMC Cancer 2021, 2015 to 2016 data; sourced; old, and a different measure from the 15-day clock).
- The standard now sets a clock, and nothing tells a provider, case by case, which positives are about to miss it ([docs/evidence.md](../evidence.md) section 8b).

## What the run is

2 to 3 clinics, 90 days, a periodic CSV or HL7 v2 export, the provider's own in-country environment. Output: a weekly list of overdue positives for coordinators and a day-90 readout of the 15-working-day referral rate, the 31-working-day colonoscopy rate, untracked positives found and flag agreement. **It sends nothing, writes nothing, assigns nothing and advises nothing.** Modelled cost to serve about AED 106,000 (arithmetic on assumed inputs), offered at cost.

## Safeguards

- A case cannot close without evidence and a physician's own signature; no automated actor can close or approve a message (property-tested and model-checked).
- Messaging is off until a clinical lead signs the outreach policy. It is unsigned, so nothing is sent automatically.
- Every clinical threshold is a placeholder until a clinician signs it: [thresholds-to-sign.md](../clinical/thresholds-to-sign.md).
- Any message sent, write made or privacy incident stops the run.

## What is not known

- No human clinician or native Arabic speaker has reviewed anything; the reviews on file are AI-simulated.
- No real data has been used. The simulated gain in colonoscopy within 180 days (51.0% to 66.8%) was fitted to published trial results and is not evidence of effect.
- The labour target of 40% fewer staff minutes is **not met** (19.7% at programme level, 36.0% where the system reaches a case), and wage-weighted staff cost per case rises about 11%. The case is capacity redeployment and a visible clock, not savings.
- Hosting, ADHICS and data-protection requirements rest partly on secondary sources and are to be verified ([go-to-market.md](go-to-market.md) section 7).

## Day-90 gate

Go to an active-pilot proposal only if export completeness and ownership visibility are at least 95%, coordinators agree with at least 80% of flags, nothing was sent or written, and the sponsor judges the untracked positives worth acting on. Otherwise stop and delete the data. Details: [pilot-offer.md](pilot-offer.md).
