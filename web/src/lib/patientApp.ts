/**
 * Pure helpers for the mobile patient app: what "my next step" is, the booked appointment, the calendar file (.ics), the plain
 * status timeline and date formatting. No React, no network, so they are unit-tested on their own (tests/patientApp.test.ts).
 *
 * Nothing here reads a staff-only field: the patient's answer from the API is already redacted (SEC-2), and these helpers
 * only use what that answer carries (the state, the allow-listed events, the owner's care contact).
 */
import type { CaseEvent, Lang } from "../api/contract";
import { stateIndex } from "./pathway";
import type { PathwayStrings, PatientStrings } from "../i18n/strings";

/** The pathway packs the app words by name; any other pack gets the plain "appointment" wording (`other`). */
export function pathwayKey(pathway?: string | null): "colorectal_fit" | "cervical_hpv" | "chronic_conditions" | "other" {
  return pathway === "colorectal_fit" || pathway === "cervical_hpv" || pathway === "chronic_conditions" ? pathway : "other";
}
export function pathwayStrings(t: PatientStrings, pathway?: string | null): PathwayStrings {
  return t.pathways[pathwayKey(pathway)];
}

/** Where the journey strip stands: 0 result received, 1 care team ready, 2 the appointment, 3 the review. */
export function journeyStep(state: string): number {
  const i = stateIndex(state);
  if (i <= 0) return 0;
  if (i <= 5) return 1; // acknowledged .. confirmatory_booked
  if (i <= 6) return 2; // confirmatory_completed
  return 3;
}

/** What the "My next step" card says and which single button it offers. */
export type NextStepKind = "preContact" | "contacted" | "arranging" | "booked" | "done" | "closed" | "withdrawn" | "working";
export interface NextStep {
  kind: NextStepKind;
  /** The one primary action, if any: confirm (acknowledge the message), add the booked appointment to the calendar, or allow contact again. */
  primary: "acknowledge" | "calendar" | "resume" | null;
}

export function nextStepOf(state: string, hasAppointment: boolean, withdrawn: boolean): NextStep {
  if (withdrawn || state === "consent_withdrawn") return { kind: "withdrawn", primary: "resume" };
  if (state === "closed") return { kind: "closed", primary: null };
  const i = stateIndex(state);
  if (state === "patient_contacted") return { kind: "contacted", primary: "acknowledge" };
  if (state === "confirmatory_booked") return { kind: "booked", primary: hasAppointment ? "calendar" : null };
  if (i < 0) return { kind: "working", primary: null }; // a side state (referral being re-routed, reopened...): neutral wording
  if (i >= stateIndex("confirmatory_completed")) return { kind: "done", primary: null };
  if (i >= stateIndex("patient_acknowledged")) return { kind: "arranging", primary: null };
  return { kind: "preContact", primary: "acknowledge" }; // shown disabled until the care team has contacted the patient
}

export interface Appointment {
  /** `YYYY-MM-DD` for a date only, else the ISO date-time. */
  when: string;
  /** True when the booking carries a time of day. */
  hasTime: boolean;
}

/** The latest booking the patient may see (`book_confirmatory` carries `appointment_date` and `appointment_at`), if the case is still booked. */
export function appointmentOf(events: CaseEvent[], state: string): Appointment | null {
  if (state !== "confirmatory_booked") return null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type !== "book_confirmatory") continue;
    const at = typeof e.payload.appointment_at === "string" ? e.payload.appointment_at : null;
    const date = typeof e.payload.appointment_date === "string" ? e.payload.appointment_date : null;
    const when = at ?? date;
    if (!when) return null;
    return { when, hasTime: /T\d/.test(when) };
  }
  return null;
}

const locale = (lang: Lang) => (lang === "ar" ? "ar-AE-u-nu-latn" : "en-GB");
/** UAE time: the programme runs in Abu Dhabi. */
const TZ = "Asia/Dubai";

export function formatAppointment(a: Appointment, lang: Lang): string {
  const opts: Intl.DateTimeFormatOptions = a.hasTime
    ? { dateStyle: "full", timeStyle: "short", timeZone: TZ }
    : { dateStyle: "full", timeZone: "UTC" }; // a date only: no zone shift
  return new Intl.DateTimeFormat(locale(lang), opts).format(new Date(a.hasTime ? a.when : `${a.when}T00:00:00Z`));
}

export function formatWhen(iso: string, lang: Lang): string {
  return new Intl.DateTimeFormat(locale(lang), { dateStyle: "medium", timeStyle: "short", timeZone: TZ }).format(new Date(iso));
}

// ---- calendar file ---------------------------------------------------------------------------------------------------

const pad = (n: number) => String(n).padStart(2, "0");
const icsStamp = (d: Date) => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}Z`;
const icsDate = (d: Date) => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}`;
/** RFC 5545 TEXT escaping. */
const icsText = (s: string) => s.replace(/\\/g, "\\\\").replace(/;/g, "\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
/** Lines longer than 75 octets are folded (RFC 5545 3.1); folding by characters is safe at this length for our short fields. */
function fold(line: string): string[] {
  const out: string[] = [];
  let rest = line;
  while (rest.length > 70) {
    out.push(rest.slice(0, 70));
    rest = ` ${rest.slice(70)}`;
  }
  out.push(rest);
  return out;
}

/**
 * An iCalendar (.ics) file for the booked appointment, built on the phone: nothing is sent anywhere. A date only becomes an
 * all-day event; a date-time a one-hour event. `uid` is stable for the case so adding it twice updates, not duplicates.
 * The text carries no finding: the pathway's appointment name and the care team's "call us if you cannot come".
 */
export function buildIcs(opts: { uid: string; title: string; description: string; appointment: Appointment; now?: Date }): string {
  const now = opts.now ?? new Date();
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Screening Closure//Patient app//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "BEGIN:VEVENT",
    `UID:${opts.uid}@screening-closure.invalid`, `DTSTAMP:${icsStamp(now)}`];
  if (opts.appointment.hasTime) {
    const start = new Date(opts.appointment.when);
    const end = new Date(start.getTime() + 3_600_000);
    lines.push(`DTSTART:${icsStamp(start)}`, `DTEND:${icsStamp(end)}`);
  } else {
    const start = new Date(`${opts.appointment.when}T00:00:00Z`);
    const end = new Date(start.getTime() + 86_400_000);
    lines.push(`DTSTART;VALUE=DATE:${icsDate(start)}`, `DTEND;VALUE=DATE:${icsDate(end)}`);
  }
  lines.push(`SUMMARY:${icsText(opts.title)}`, `DESCRIPTION:${icsText(opts.description)}`, "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR");
  return `${lines.flatMap(fold).join("\r\n")}\r\n`;
}

// ---- status timeline -------------------------------------------------------------------------------------------------

export type HistoryKey = keyof PatientStrings["history"];
export interface HistoryItem { key: HistoryKey; at: string; text: string }

/**
 * The status timeline in plain language: one sentence for each step the patient may see, oldest first. Event types the
 * patient is not shown have no sentence and are skipped (they are not in a patient's answer anyway).
 */
export function historyOf(events: CaseEvent[], t: PatientStrings): HistoryItem[] {
  const out: HistoryItem[] = [];
  for (const e of events) {
    if (!Object.hasOwn(t.history, e.type)) continue;
    const key = e.type as HistoryKey;
    out.push({ key, at: e.at, text: t.history[key] });
  }
  return out;
}

/** The plain name of a role for a patient (the owner, or someone who opened the record); unknown roles read "care team". */
export function roleName(t: PatientStrings, role: string): string {
  return Object.hasOwn(t.roles, role) ? (t.roles as Record<string, string>)[role] : t.roles.other;
}

/** A `tel:` link for a phone number written with spaces. */
export const telHref = (phone: string) => `tel:${phone.replace(/[^+\d]/g, "")}`;
