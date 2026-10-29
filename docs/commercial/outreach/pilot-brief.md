# Pilot brief: closing the loop on positive screening results

*Prototype on synthetic data. Not a diagnostic tool. Draft for discussion.*

## The problem

A positive screening result is only the start. An audit of Abu Dhabi ambulatory clinics found that only 30.5% of 1,429 positive FIT results ever had the follow-up colonoscopy (Almansoori et al., BMC Cancer 2021, 2015-2016 data). The largest leak was patients who were referred but did not attend. Nobody was watching the clock.

## What the engine does

For every positive result it gives a named owner, a clock against the official timelines, an audit trail, and a closure that a clinician signs. It starts with colorectal FIT. It does not diagnose and gives no clinical advice.

## The offer

A 90-day, read-only shadow run on 2 to 3 of your clinics.

- It reads a periodic HL7 v2 or CSV export of positives, referrals, appointments and colonoscopy reports.
- It sends nothing to patients.
- It writes nothing to your systems.
- It assigns no task to anyone. Coordinators work as they do today.
- It runs in your own approved environment in the UAE.

## What you get at day 90

- A baseline of your own follow-up against the IFHAS 15-working-day referral timeline and the blood-test 7-day (GI consultation) and 30-day (colonoscopy) timelines.
- A list of positives nobody was tracking.
- Your coordinators' own judgement of how accurate the flags were.
- Staff minutes by role.

## What we need

- A sponsor to sign a non-binding letter of intent.
- A periodic export, and a data-sharing agreement signed before any real data moves.
- A coordinator for a weekly 30-minute review of flags.

## Exit criterion

Targets are agreed before day 1. You proceed to an active pilot proposal only if export completeness and ownership are at least 95%, flag agreement is at least 80%, and there are zero patient contacts, writes and privacy incidents. Otherwise we stop and delete or return all data within 30 days. A stop is a valid result.

## Cost

Offered at cost: about AED 106,000 to serve 3 clinics for 90 days, or less for one clinic. This is an assumed cost, not a quote.

## Honest limits

- Results so far are from synthetic data only.
- No clinician has reviewed the pathway yet.
- There is no buyer yet.
- It is not connected to Malaffi, and the shadow run does not need it.

## Contact

[Your name], [role], [email], [phone]. Prototype: [link to repo or demo video].

More detail: [go-to-market](../go-to-market.md), [buyer map](../buyer-map.md), [letter of intent](letter-of-intent.md).
