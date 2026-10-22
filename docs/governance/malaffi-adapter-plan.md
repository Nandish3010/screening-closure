# Malaffi adapter plan

**Status: a target, not a claim.** The prototype is not connected to Malaffi, has not been tested with it, and has no agreement with its operator. Everything below is a plan for what a pilot would need and what we assume. Facts about Malaffi are from its public pages (accessed 2026-10-18) and are listed with their limits in [regulatory-mapping.md](regulatory-mapping.md), section 5.

## Position

Our engine is FHIR R4 inside (ADR-008). Malaffi's public materials point to **HL7 v2 messages** at its boundary: its resources page lists HL7 message specifications available on request, and its onboarding page requires an HL7-capable EMR without mentioning FHIR. DoH Circular 2026/10 (19 January 2026), read first-hand in the official PDF (check P, [evidence.md](../evidence.md) section 8c), expresses the Malaffi minimum dataset as HL7 message types (ADT, PPR, ORU, OMP, RDS, RAS, VXU, SIU). So the plan is: **HL7 v2 at the Malaffi boundary through an adapter; FHIR toward Malaffi only if the operator confirms it.** FHIR support is **to confirm with the HIE operator (hie@doh.gov.ae, a contact printed on the DoH pages, see evidence.md Gaps item 4); the interface specification itself and any FHIR availability are to verify**.

## What would be exchanged

| Our FHIR R4 resource (internal) | Direction | HL7 v2 message at the boundary (assumed) | Purpose | Confidence |
|---|---|---|---|---|
| `DiagnosticReport` + `Observation` (FIT result, LOINC 29771-3) | In | ORU (observation result) | Starts a case; or supplies colonoscopy results as closure evidence | Type named in Circular 2026/10; segment-level mapping to verify against the operator's specification |
| `Patient` (demographics) | In | ADT | Identity and contact details for matching | As above |
| `Appointment` (booked, attended, missed) | In | SIU (scheduling information) | Moves a case to confirmatory booked; detects a no-show | As above |
| `ServiceRequest` / `Task` (referral and ownership status) | Out, if the operator permits | Not a native HL7 v2 event; candidate is the provider's EMR referral order, or no exchange at all | Tell the receiving provider who owns the case and what is due | To confirm. It is not known that Malaffi accepts workflow status from a non-provider system. |
| Closure documentation (colonoscopy report) | In | A document message type, if offered | Evidence reference for closure | To confirm |
| `Communication` (patient messages) | None | Stays inside our system and the provider's contact channel | Not clinical data for the exchange | By design |
| `Consent` (channel and sharing consent) | None | Internal ledger only. Malaffi has its own patient consent rules, which we must honour | | Malaffi rules to verify |

A result received through the exchange never closes a case. It can be attached as evidence; closure still needs a clinician's sign-off (ADR-001).

## Adapter boundary in our architecture

```
Malaffi / provider EMR  --HL7 v2-->  [hie adapter]  --FHIR R4 + events-->  API  -->  event log
                                            ^                                  |
                                            +------ outbox (status out) -------+
```

- **Placement.** A separate service outside the engine core. The engine and state machine know nothing about HL7. The adapter only calls the same API routes any client uses, under an automated actor (`system:hie-adapter`), which by the existing rule cannot emit `close` or sign anything.
- **Inbound.** Parse the message, validate against the operator's specification, map to an internal FHIR resource, then call the API to create a case or attach evidence. The message control id is the idempotency key (`event_id`), so redelivery changes nothing.
- **Outbound.** Read the event stream through an outbox; a delivery failure is retried and then dead-lettered and shown to a person. Nothing is dropped silently.
- **Failure visibility.** The watchdog treats the interface like the scheduler: if no message has arrived for a configured period, or the dead-letter queue is non-empty, it raises a flag. Silence on the interface is the dangerous failure.
- **Data location.** The adapter runs inside the same in-country environment as the engine (regulatory-mapping section 6).
- **Status in the repository.** Partly built (A28, M6, ADR-039), against synthetic messages only. [hl7-adapter.md](hl7-adapter.md) describes what exists and what does not, and is the reference where it differs from this plan.
  - **Built:** `POST /hl7` (the message as text, the ACK as the answer); an optional MLLP listener (compose profile `hl7`, off by default, loopback only) that posts to the same route; a standard-library parser and ACK writer; ORU^R01 for a FIT result (creates a case) and for a colonoscopy report (evidence only); SIU S12 and S14 (book the confirmatory appointment); ADT A08 and A31 (contact validity only); idempotency on MSH-10; exact identifier matching with no fuzzy match; no PHI in logs or ACKs; the actor `system:hl7`, with three actions and one event (`book_confirmatory`), which cannot close a case; synthetic samples and a demo (`tools/hl7/`, `make hl7-demo`). The actor is named `system:hl7` here, not `system:hie-adapter` as the placement bullet above says.
  - **Not built:** any connection to Malaffi, any test with it, and any agreement with its operator; the operator's segment-level specifications were not seen, so every mapping is our reading of the standard. No outbound messages, outbox or dead-letter queue. No certificates, VPN, TLS or sender authentication beyond a bearer token, and no interface watchdog. SIU S15 (cancel), and a different time for an already booked case, are ACKed AE, because the pathway has no cancel or rebook event. No Emirates-ID lookup or human queue for ambiguous matches; no FHIR `Observation` or `DiagnosticReport` is created for the FIT result; the contact registry is in memory; the genesis event does not record that a case came from HL7; ORM, RDS, RAS, VXU, PPR and OMP are not handled.

## Identity matching assumptions

- A national identifier (Emirates ID is the likely key; **assumed, to confirm**) is the primary match key when present in the message. The prototype has none: its patients are synthetic and use US-style identifiers.
- Without a reliable identifier, match on name, date of birth and sex, and send ambiguous matches to a human queue. **Never auto-merge and never auto-attach** a result to a patient on a fuzzy match; a wrong-patient attachment is a listed hazard (E6).
- Duplicate results from two sources are linked by identifier and timestamp and reported, not silently merged.
- Arabic and English name variants and transliteration are expected; matching rules need real data from the pilot and are not designed in detail here.

## What a pilot would need from the HIE operator

1. Written confirmation of the interface standard offered to a non-EMR workflow vendor (HL7 v2, FHIR, or both) and the message specifications, including the minimum dataset.
2. Whether the participation route for our service is direct or through a connected provider's EMR, the accession agreement and participation criteria, and any fees.
3. A test environment with synthetic or de-identified messages, and the certification steps before live traffic.
4. Consent and opt-out rules as they apply to a workflow system, and what the exchange returns for an opted-out patient.
5. Whether the exchange accepts status information (ownership, due dates) or only clinical data, and the permitted use of exchange data for follow-up tracking.
6. Security and assessment requirements for the vendor (the Malaffi security assessment is described as an excerpt of the ADHICS survey), the network connection arrangement, and incident-reporting duties.
7. Service levels, message delay and downtime notices, so the watchdog thresholds can be set.
8. A named technical contact and a change-notification route.

The decision on whether any of this is feasible rests with DoH and the operator; the pilot plan ([pilot-plan.md](../pilot-plan.md)) therefore treats Malaffi connectivity as optional and runs on the provider's own feed first.
