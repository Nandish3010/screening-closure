import { describe, expect, it } from "vitest";
import type { CaseEvent } from "../src/api/contract";
import { STRINGS } from "../src/i18n/strings";
import {
  appointmentOf, buildIcs, formatAppointment, historyOf, journeyStep, nextStepOf, pathwayKey, pathwayStrings, roleName, telHref,
} from "../src/lib/patientApp";

const ev = (type: string, payload: Record<string, unknown> = {}, at = "2026-11-01T08:00:00Z"): CaseEvent => ({ seq: 1, type, at, actor: "coordinator", payload });

describe("my next step: one primary action", () => {
  it("is 'confirm' (acknowledge) before and after the care team contacts the patient, the calendar when booked, none after", () => {
    expect(nextStepOf("result_received", false, false)).toEqual({ kind: "preContact", primary: "acknowledge" });
    expect(nextStepOf("patient_contacted", false, false)).toEqual({ kind: "contacted", primary: "acknowledge" });
    expect(nextStepOf("patient_acknowledged", false, false)).toEqual({ kind: "arranging", primary: null });
    expect(nextStepOf("confirmatory_booked", true, false)).toEqual({ kind: "booked", primary: "calendar" });
    expect(nextStepOf("confirmatory_booked", false, false)).toEqual({ kind: "booked", primary: null });
    expect(nextStepOf("confirmatory_completed", false, false).kind).toBe("done");
    expect(nextStepOf("clinician_reviewed", false, false).kind).toBe("done");
    expect(nextStepOf("closed", false, false)).toEqual({ kind: "closed", primary: null });
  });
  it("withdrawn consent makes 'allow contact again' the one action, whatever the state", () => {
    expect(nextStepOf("consent_withdrawn", false, false)).toEqual({ kind: "withdrawn", primary: "resume" });
    expect(nextStepOf("patient_contacted", false, true)).toEqual({ kind: "withdrawn", primary: "resume" });
  });
  it("a side state (re-routed referral, reopened) reads neutrally", () => {
    for (const s of ["referral_rejected", "reopened", "lost_contact", "declined_pending_signoff"]) expect(nextStepOf(s, false, false)).toEqual({ kind: "working", primary: null });
  });
  it("the journey strip moves with the state", () => {
    expect([journeyStep("result_received"), journeyStep("owner_accepted"), journeyStep("confirmatory_booked"), journeyStep("confirmatory_completed"), journeyStep("closed")]).toEqual([0, 1, 1, 2, 3]);
  });
});

describe("the booked appointment", () => {
  it("is the latest booking, only while the case is booked", () => {
    const events = [ev("book_confirmatory", { appointment_date: "2026-11-10" }), ev("book_confirmatory", { appointment_date: "2026-11-12", appointment_at: "2026-11-12T06:30:00Z" })];
    expect(appointmentOf(events, "confirmatory_booked")).toEqual({ when: "2026-11-12T06:30:00Z", hasTime: true });
    expect(appointmentOf(events.slice(0, 1), "confirmatory_booked")).toEqual({ when: "2026-11-10", hasTime: false });
    expect(appointmentOf(events, "patient_acknowledged")).toBeNull(); // cancelled or past: not shown
    expect(appointmentOf([ev("book_confirmatory", {})], "confirmatory_booked")).toBeNull();
  });
  it("formats a date-only booking without a time-zone shift, in both languages", () => {
    expect(formatAppointment({ when: "2026-11-12", hasTime: false }, "en")).toMatch(/12 November 2026/);
    const ar = formatAppointment({ when: "2026-11-12", hasTime: false }, "ar");
    expect(ar).toMatch(/[؀-ۿ]/);
    expect(ar).toMatch(/12/);
  });
  it("formats a booked time in UAE time", () => {
    expect(formatAppointment({ when: "2026-11-12T06:30:00Z", hasTime: true }, "en")).toMatch(/10:30/);
  });
});

describe("the calendar file", () => {
  const now = new Date("2026-11-05T09:00:00Z");
  it("a date becomes an all-day event", () => {
    const ics = buildIcs({ uid: "case-1", title: "Camera test (colonoscopy)", description: "Booked, call us; if you cannot come", appointment: { when: "2026-11-12", hasTime: false }, now });
    expect(ics.startsWith("BEGIN:VCALENDAR\r\n")).toBe(true);
    expect(ics.endsWith("END:VCALENDAR\r\n")).toBe(true);
    expect(ics).toContain("DTSTART;VALUE=DATE:20261112");
    expect(ics).toContain("DTEND;VALUE=DATE:20261113");
    expect(ics).toContain("UID:case-1@screening-closure.invalid");
    expect(ics).toContain("DTSTAMP:20261105T090000Z");
    expect(ics).toContain("SUMMARY:Camera test (colonoscopy)");
    expect(ics).toContain("DESCRIPTION:Booked\\, call us\; if you cannot come"); // RFC 5545 escaping
  });
  it("a date-time becomes a one-hour event in UTC", () => {
    const ics = buildIcs({ uid: "c", title: "T", description: "D", appointment: { when: "2026-11-12T06:30:00Z", hasTime: true }, now });
    expect(ics).toContain("DTSTART:20261112T063000Z");
    expect(ics).toContain("DTEND:20261112T073000Z");
  });
  it("lines are CRLF, folded under 75 characters, and Arabic text survives", () => {
    const ics = buildIcs({ uid: "c", title: STRINGS.ar.pathways.colorectal_fit.calendarTitle, description: STRINGS.ar.calendarDescription + " ".repeat(5) + STRINGS.ar.calendarNote, appointment: { when: "2026-11-12", hasTime: false }, now });
    for (const line of ics.split("\r\n")) expect(line.length).toBeLessThanOrEqual(75);
    expect(ics.replace(/\r\n /g, "")).toContain(STRINGS.ar.pathways.colorectal_fit.calendarTitle);
    expect(ics).not.toMatch(/[^\r]\n/);
  });
  it("carries no finding: only the appointment name and the call-us line", () => {
    for (const lang of ["en", "ar"] as const) {
      const t = STRINGS[lang];
      for (const key of ["colorectal_fit", "cervical_hpv", "chronic_conditions", "other"] as const) {
        const ics = buildIcs({ uid: "c", title: t.pathways[key].calendarTitle, description: t.calendarDescription, appointment: { when: "2026-11-12", hasTime: false }, now });
        expect(ics).not.toMatch(/blood|cancer|positive|abnormal|diagnos|found|worry|دم|سرطان|ايجابي|مصاب|تشخيص/i);
      }
    }
  });
});

describe("the three pathways use their own wording", () => {
  it("bowel names the camera test, cervical a follow-up examination, long-term conditions the family doctor", () => {
    expect(pathwayStrings(STRINGS.en, "colorectal_fit").nextSteps.join(" ")).toMatch(/colonoscopy/);
    expect(pathwayStrings(STRINGS.en, "cervical_hpv").nextSteps.join(" ")).toMatch(/follow-up examination/);
    expect(pathwayStrings(STRINGS.en, "chronic_conditions").nextSteps.join(" ")).toMatch(/family doctor/);
    expect(pathwayStrings(STRINGS.ar, "cervical_hpv").progress[2]).toBe("فحص المتابعة");
    expect(pathwayStrings(STRINGS.ar, "chronic_conditions").appointmentTitle).toMatch(/طبيب الأسرة/);
  });
  it("an unknown pathway gets the plain 'appointment' wording and never crashes", () => {
    expect(pathwayKey("something_new")).toBe("other");
    expect(pathwayKey(undefined)).toBe("other");
    expect(pathwayStrings(STRINGS.en, "__proto__").appointmentTitle).toBe("Your appointment");
  });
  it("no pathway line states or hints at a finding (E14), in either language", () => {
    for (const lang of ["en", "ar"] as const) {
      const all = JSON.stringify(Object.values(STRINGS[lang].pathways));
      expect(all).not.toMatch(/blood|cancer|positive|abnormal|diagnos|found|worry|\bnormal\b|دم|سرطان|ايجابي|إيجابي|مصاب|تشخيص|طبيعي|تقلق/i);
    }
  });
});

describe("the status timeline", () => {
  it("is one plain sentence per visible step, in order, and skips what has no sentence", () => {
    const t = STRINGS.en;
    const items = historyOf([ev("case_created"), ev("acknowledge"), ev("something_internal"), ev("contact_patient"), ev("consent_updated")], t);
    expect(items.map((i) => i.text)).toEqual([t.history.case_created, t.history.acknowledge, t.history.contact_patient, t.history.consent_updated]);
    expect(historyOf([ev("__proto__"), ev("constructor"), ev("toString")], t)).toEqual([]);
  });
  it("every sentence exists in both languages and names no finding", () => {
    expect(Object.keys(STRINGS.ar.history)).toEqual(Object.keys(STRINGS.en.history));
    for (const lang of ["en", "ar"] as const) expect(JSON.stringify(STRINGS[lang].history)).not.toMatch(/blood|cancer|positive|abnormal|diagnos|found|worry|دم|سرطان|ايجابي|مصاب|تشخيص/i);
  });
});

describe("small helpers", () => {
  it("role names fall back to the care team; the phone link keeps only digits and +", () => {
    expect(roleName(STRINGS.en, "primary_care_owner")).toBe("Primary care doctor");
    expect(roleName(STRINGS.en, "toString")).toBe("Care team");
    expect(roleName(STRINGS.ar, "anything")).toBe(STRINGS.ar.roles.other);
    expect(telHref("+971 2 000 0000")).toBe("tel:+97120000000");
  });
});

describe("Arabic: no word with the letters of the banned substring anywhere in the patient catalogue", () => {
  it("never contains the substring the E14 rule bans", () => {
    const walk = (v: unknown): string[] => (typeof v === "function" ? [(v as (a: string, b: string) => string)("X", "Y")] : typeof v === "string" ? [v] : Array.isArray(v) ? v.flatMap(walk) : v && typeof v === "object" ? Object.values(v).flatMap(walk) : []);
    for (const s of walk(STRINGS.ar)) expect(s, s).not.toContain("دم");
  });
});
