import type { IntegrationFeed, IntegrationFeedEntry } from "../api/contract";

/**
 * The mock adapter's stand-in for `GET /integrations/feed`: a fixed sample of the same metadata-only entries the API records
 * (a kind, a direction, a fixed phrase, a status, a short hash). It is generated from nothing and read from nothing: it is
 * labelled simulated and synthetic, like the real feed. The summaries mirror what the HL7 handler, the FHIR projector and the
 * notification mock write (see api/app/integration_feed.py).
 */
const SAMPLE: Omit<IntegrationFeedEntry, "seq" | "at">[] = [
  { kind: "hl7", direction: "in", summary: "HL7 ORU^R01 received: case created", status: "ACK AA", ref: "3f9a1c7b2d04" },
  { kind: "fhir", direction: "out", summary: "FHIR transaction: 3 resources (Provenance, Task)", status: "ok", ref: null },
  { kind: "notification", direction: "out", summary: "sms message (result_ready, en)", status: "sent", ref: "9b1f4a2c" },
  { kind: "hl7", direction: "in", summary: "HL7 SIU^S12 received: appointment booked", status: "ACK AA", ref: "a7d0c55e19b2" },
  { kind: "fhir", direction: "in", summary: "FHIR evidence lookup: DiagnosticReport", status: "found", ref: null },
  { kind: "notification", direction: "out", summary: "whatsapp message (reminder, ar)", status: "blocked_consent", ref: "2a7f4b9d" },
  { kind: "hl7", direction: "in", summary: "HL7 ORU^R01 received: preliminary result not applied", status: "ACK AE", ref: "c41e6d03f7a8" },
  { kind: "fhir", direction: "out", summary: "FHIR transaction not written; queued for retry", status: "queued", ref: null },
];

export function mockIntegrationFeed(limit: number, simNow: string): IntegrationFeed {
  const n = Math.max(1, Math.min(Math.floor(limit) || 1, 50));
  const base = new Date(simNow).getTime();
  const entries = SAMPLE.map((e, i) => ({ ...e, seq: SAMPLE.length - i, at: new Date(base - i * 90_000).toISOString() })).slice(0, n);
  return {
    simulated: true,
    synthetic: true,
    scope: "a fixed sample in the browser mock; the real API lists the last messages of its own process",
    max_entries: SAMPLE.length,
    entries,
  };
}
