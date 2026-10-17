/**
 * Seeded fixtures for the mock adapter. ALL DATA IS SYNTHETIC (invented names, no real people).
 * Sim "now" is fixed so every demo and test is deterministic.
 */
import type { CaseEvent, ClosureMetrics } from "./contract";

export const SIM_NOW = "2026-11-04T09:00:00.000Z";
const DAY = 86_400_000;
const at = (daysAgo: number) => new Date(new Date(SIM_NOW).getTime() - daysAgo * DAY).toISOString();

export const DEMO_PATIENT_CASE_ID = "c0000007-0000-4000-8000-000000000007";
/** Most overdue case: sits at confirmatory_completed, ready for escalate -> draft -> sign-off. */
export const DEMO_OVERDUE_CASE_ID = "c0000001-0000-4000-8000-000000000001";

type Step = [type: string, daysAgo: number, actor: string, payload?: Record<string, unknown>];

function build(id: string, createdDaysAgo: number, steps: Step[]): CaseEvent[] {
  const evs: CaseEvent[] = [
    { seq: 1, type: "case_created", at: at(createdDaysAgo), actor: "system:intake", payload: { patient_ref: `synthetic-patient-${id.slice(-3)}` } },
  ];
  steps.forEach(([type, d, actor, payload], i) =>
    evs.push({ seq: i + 2, type, at: at(d), actor, payload: payload ?? {} }),
  );
  return evs;
}

const LAYLA = { owner: "Dr. Layla Haddad", owner_role: "Primary care physician" };
const SARA = { owner: "Sara Al Mansoori", owner_role: "Care coordinator" };
const OMAR = { owner: "Omar Khalid", owner_role: "Care coordinator" };

export const FIXTURE_RISK: Record<string, number> = {};
const defs: { id: string; risk: number; events: CaseEvent[] }[] = [];
function add(n: number, risk: number, createdDaysAgo: number, steps: Step[]) {
  const id = `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
  FIXTURE_RISK[id] = risk;
  defs.push({ id, risk, events: build(id, createdDaysAgo, steps) });
}

// 1. Most overdue: confirmatory completed 30 days ago, clinician review (5 working days) not done.
add(1, 0.62, 95, [
  ["acknowledge", 94, "lab:Central Lab"],
  ["accept_ownership", 92, "coordinator:Maryam Al Suwaidi", LAYLA],
  ["draft_generated", 91, "model:template-fallback", { draft_id: "d-seed-1", provider: "template-fallback", fallback_used: true,
    text_en: "Hello. Your screening test needs a follow-up camera test. Please call us to book it.",
    text_ar: "مرحبًا. يحتاج فحصك إلى فحص متابعة بالكاميرا. يرجى الاتصال بنا لحجز الموعد." }],
  ["draft_approved", 91, "coordinator:Maryam Al Suwaidi", { draft_id: "d-seed-1", approved_by: "Maryam Al Suwaidi" }],
  ["contact_patient", 90, "coordinator:Maryam Al Suwaidi", { channel: "phone", draft_id: "d-seed-1" }],
  ["patient_acknowledge", 86, "patient:c0000001", { lang: "ar" }],
  ["consent_updated", 86, "patient:c0000001", { channel: "whatsapp", share_with_specialist: true }],
  ["hand_off", 80, "coordinator:Maryam Al Suwaidi", { to_provider: "Al Noor Endoscopy Centre" }],
  ["hand_off_rejected", 79, "provider:Al Noor Endoscopy Centre", { provider: "Al Noor Endoscopy Centre", reason: "No colonoscopy slots in the next 14 days." }],
  ["hand_off", 78, "coordinator:Maryam Al Suwaidi", { to_provider: "Corniche Gastro Unit" }],
  ["hand_off_accepted", 77, "provider:Corniche Gastro Unit", { provider: "Corniche Gastro Unit" }],
  ["book_confirmatory", 70, "specialist:Dr. Karim Nasser", { appointment_ref: "APPT-2026-0412" }],
  ["complete_confirmatory", 30, "specialist:Dr. Karim Nasser", { result_ref: "DiagnosticReport/colonoscopy-0001" }],
]);
// 2. Patient contacted 22 days ago; acknowledgement (7 working days) overdue.
add(2, 0.81, 40, [
  ["acknowledge", 39, "lab:Central Lab"],
  ["accept_ownership", 37, "coordinator:Maryam Al Suwaidi", SARA],
  ["contact_patient", 22, "coordinator:Maryam Al Suwaidi", { channel: "sms" }],
]);
// 3. Owner accepted 16 days ago; patient contact (5 working days) overdue.
add(3, 0.7, 25, [
  ["acknowledge", 24, "lab:Central Lab"],
  ["accept_ownership", 16, "coordinator:Maryam Al Suwaidi", OMAR],
]);
// 4. Result received 6 days ago, nobody acknowledged (2 working days) -> overdue, unowned.
add(4, 0.55, 6, []);
// 5. Acknowledged yesterday, unowned, not overdue.
add(5, 0.4, 3, [["acknowledge", 1, "lab:Central Lab"]]);
// 6. Owner accepted 2 days ago, not overdue.
add(6, 0.35, 5, [["acknowledge", 4, "lab:Central Lab"], ["accept_ownership", 2, "coordinator:Maryam Al Suwaidi", LAYLA]]);
// 7. Patient demo case: patient contacted 2 days ago, waiting for the patient to acknowledge.
add(7, 0.3, 9, [
  ["acknowledge", 8, "lab:Central Lab"],
  ["accept_ownership", 6, "coordinator:Maryam Al Suwaidi", SARA],
  ["contact_patient", 2, "coordinator:Maryam Al Suwaidi", { channel: "sms" }],
]);
// 8. Patient acknowledged 3 days ago.
add(8, 0.25, 20, [
  ["acknowledge", 19, "lab:Central Lab"],
  ["accept_ownership", 17, "coordinator:Maryam Al Suwaidi", OMAR],
  ["contact_patient", 10, "coordinator:Maryam Al Suwaidi", { channel: "phone" }],
  ["patient_acknowledge", 3, "patient:c0000008", { lang: "en" }],
]);
// 9. Confirmatory booked.
add(9, 0.2, 30, [
  ["acknowledge", 29, "lab:Central Lab"],
  ["accept_ownership", 27, "coordinator:Maryam Al Suwaidi", LAYLA],
  ["contact_patient", 24, "coordinator:Maryam Al Suwaidi", { channel: "phone" }],
  ["patient_acknowledge", 22, "patient:c0000009", { lang: "ar" }],
  ["book_confirmatory", 12, "coordinator:Maryam Al Suwaidi", { appointment_ref: "APPT-2026-0577" }],
]);
// 10. Clinician reviewed, awaiting signed closure (no SLA on close).
add(10, 0.12, 60, [
  ["acknowledge", 59, "lab:Central Lab"],
  ["accept_ownership", 58, "coordinator:Maryam Al Suwaidi", OMAR],
  ["contact_patient", 55, "coordinator:Maryam Al Suwaidi", { channel: "phone" }],
  ["patient_acknowledge", 52, "patient:c0000010", { lang: "en" }],
  ["book_confirmatory", 45, "coordinator:Maryam Al Suwaidi", { appointment_ref: "APPT-2026-0310" }],
  ["complete_confirmatory", 8, "specialist:Dr. Karim Nasser", { result_ref: "DiagnosticReport/colonoscopy-0010" }],
  ["clinician_review", 1, "clinician:Dr. Huda Rahman", { signed_by: "Dr. Huda Rahman" }],
]);
// 11. Closed with evidence and a human sign-off.
add(11, 0.05, 80, [
  ["acknowledge", 79, "lab:Central Lab"],
  ["accept_ownership", 78, "coordinator:Maryam Al Suwaidi", SARA],
  ["contact_patient", 75, "coordinator:Maryam Al Suwaidi", { channel: "sms" }],
  ["patient_acknowledge", 72, "patient:c0000011", { lang: "en" }],
  ["book_confirmatory", 65, "coordinator:Maryam Al Suwaidi", { appointment_ref: "APPT-2026-0201" }],
  ["complete_confirmatory", 40, "specialist:Dr. Karim Nasser", { result_ref: "DiagnosticReport/colonoscopy-0011" }],
  ["clinician_review", 36, "clinician:Dr. Huda Rahman", { signed_by: "Dr. Huda Rahman" }],
  ["close", 35, "clinician:Dr. Huda Rahman", { evidence_ref: "DiagnosticReport/colonoscopy-0011", signed_by: "Dr. Huda Rahman" }],
]);
// 12. Overdue not at all, but high risk: ordering tie-breaker demo (acknowledged, owner accepted today).
add(12, 0.9, 4, [["acknowledge", 3, "lab:Central Lab"], ["accept_ownership", 0, "coordinator:Maryam Al Suwaidi", SARA]]);

export function seedEvents(): Record<string, CaseEvent[]> {
  return Object.fromEntries(defs.map((d) => [d.id, d.events]));
}

export const SEED_METRICS: Omit<ClosureMetrics, "sim_time"> = {
  p50_days: 38,
  p90_days: 79,
  open_by_age_bucket: { "0-30": 41, "31-60": 23, "61-90": 11, "90+": 6 },
  breaches_by_provider: [
    { provider: "Al Noor Endoscopy Centre", count: 14 },
    { provider: "Corniche Gastro Unit", count: 9 },
    { provider: "Marina Digestive Health", count: 6 },
    { provider: "Central Lab", count: 3 },
  ],
  bottleneck_by_transition: [
    { transition: "complete_confirmatory", median_days: 31 },
    { transition: "book_confirmatory", median_days: 12 },
    { transition: "patient_acknowledge", median_days: 6 },
    { transition: "contact_patient", median_days: 4 },
    { transition: "clinician_review", median_days: 3 },
    { transition: "accept_ownership", median_days: 2 },
    { transition: "acknowledge", median_days: 1 },
  ],
};
