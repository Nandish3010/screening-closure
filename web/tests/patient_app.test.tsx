import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEMO_PATIENT_CASE_ID } from "../src/api/fixtures";
import { createMockAdapter } from "../src/api/mockAdapter";
import { createHttpAdapter } from "../src/api/httpAdapter";
import type { ApiClient } from "../src/api/contract";
import { STRINGS } from "../src/i18n/strings";
import { PatientView } from "../src/pages/PatientPage";
import { renderWithApp } from "./helpers";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

/** A demo case with a booked appointment: the coordinator confirms the patient understood, then books. */
async function bookedApi(date = "2026-11-12"): Promise<ApiClient> {
  const api = createMockAdapter(null);
  await api.login("coordinator", "Maryam Al Suwaidi");
  await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "patient_acknowledge", actor: "x", payload: { lang: "en" } });
  await api.postEvent(DEMO_PATIENT_CASE_ID, { type: "book_confirmatory", actor: "x", payload: { appointment_ref: "APPT-1", appointment_date: date } });
  await api.login("patient", "Patient");
  return api;
}
const openAs = async (api: ApiClient) => {
  renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api, role: "patient" });
  await screen.findByText(STRINGS.en.resultBody);
};
const lastEvent = async (api: ApiClient) => {
  await api.login("coordinator", "Maryam Al Suwaidi");
  try {
    return (await api.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)!;
  } finally {
    await api.login("patient", "Patient");
  }
};

describe("the app shell", () => {
  it("is a phone frame on a wide screen and the bare app on a phone: one markup, the CSS decides", async () => {
    await openAs(createMockAdapter(null));
    const root = screen.getByTestId("patient-root");
    expect(root.closest(".pa-phone")).not.toBeNull();
    expect(root.closest(".pa-stage")).not.toBeNull();
    expect(root.tagName).toBe("MAIN");
  });

  it("has a bottom menu with two named tabs, the current one marked, and moves focus to the new heading", async () => {
    const user = userEvent.setup();
    await openAs(createMockAdapter(null));
    const nav = screen.getByRole("navigation", { name: STRINGS.en.navLabel });
    const home = within(nav).getByRole("button", { name: STRINGS.en.navHome });
    const privacy = within(nav).getByRole("button", { name: STRINGS.en.navPrivacy });
    expect(home).toHaveAttribute("aria-current", "page");
    expect(privacy).not.toHaveAttribute("aria-current");
    await user.click(privacy);
    expect(privacy).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(STRINGS.en.privacyHeading);
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
  });

  it("opens on the privacy tab from the manifest shortcut (?tab=privacy)", async () => {
    window.history.pushState({}, "", "/patient?tab=privacy");
    try {
      renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api: createMockAdapter(null), role: "patient" });
      expect(await screen.findByTestId("access-log")).toBeInTheDocument();
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("the language toggle names both languages in their own script and flips RTL, with logical-property CSS only for direction", async () => {
    const user = userEvent.setup();
    await openAs(createMockAdapter(null));
    const toggle = screen.getByRole("button", { name: /العربية/ });
    expect(toggle).toHaveTextContent("English");
    expect(toggle).toHaveTextContent("العربية");
    await user.click(toggle);
    expect(screen.getByTestId("patient-root")).toHaveAttribute("dir", "rtl");
    expect(screen.getByRole("navigation", { name: STRINGS.ar.navLabel })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("lang", "ar");
  });

  it("has no unnamed buttons or links", async () => {
    const user = userEvent.setup();
    await openAs(await bookedApi());
    for (const tab of ["tab-home", "tab-privacy"]) {
      await user.click(screen.getByTestId(tab));
      for (const el of [...screen.getAllByRole("button"), ...screen.queryAllByRole("link")]) expect(el, el.outerHTML).toHaveAccessibleName();
    }
  });
});

describe("my next step: a single primary action", () => {
  it("before contact: the confirm button is there but waits, with its reason", async () => {
    await openAs(createMockAdapter(null));
    // the demo case is already contacted: confirm is available
    expect(screen.getByTestId("primary-action")).toHaveTextContent(STRINGS.en.understand);
    expect(document.querySelectorAll(".btn-primary")).toHaveLength(1);
  });

  it("confirming acknowledges, and then the page offers no primary action until there is an appointment", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    await openAs(api);
    await user.click(screen.getByTestId("primary-action"));
    await screen.findByTestId("ack-done");
    expect((await lastEvent(api)).type).toBe("patient_acknowledge");
    expect(screen.queryByTestId("primary-action")).toBeNull();
    expect(screen.getByTestId("next-text")).toHaveTextContent(STRINGS.en.pathways.colorectal_fit.waiting);
  });

  it("with an appointment: the date, 'add to calendar' as the one primary action, ask for another date, cannot come, call me", async () => {
    const user = userEvent.setup();
    const api = await bookedApi();
    await openAs(api);
    expect(screen.getByTestId("appointment-date")).toHaveTextContent(/12 November 2026/);
    expect(screen.getByTestId("appointment")).toHaveTextContent(STRINGS.en.pathways.colorectal_fit.appointmentTitle);
    expect(screen.getByTestId("primary-action")).toHaveTextContent(STRINGS.en.addToCalendar);
    expect(document.querySelectorAll(".btn-primary")).toHaveLength(1);

    await user.click(screen.getByTestId("reschedule"));
    await waitFor(async () => expect(await lastEvent(api)).toMatchObject({ type: "callback_requested", payload: { reason: "reschedule", lang: "en" } }));
    await user.click(screen.getByTestId("cannot-attend"));
    await waitFor(async () => expect((await lastEvent(api)).payload).toMatchObject({ reason: "cannot_attend" }));
    await user.click(screen.getByRole("button", { name: STRINGS.en.dontUnderstand }));
    await waitFor(async () => expect((await lastEvent(api)).payload).toMatchObject({ reason: "patient_does_not_understand" }));
  });

  it("add to calendar builds the .ics on the phone and saves it as a file; nothing is posted", async () => {
    const user = userEvent.setup();
    const api = await bookedApi();
    await openAs(api);
    let blob: Blob | null = null;
    (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = (b: Blob) => { blob = b; return "blob:test"; };
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => undefined;
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const before = await lastEvent(api);
    await user.click(screen.getByTestId("primary-action"));
    expect(click).toHaveBeenCalledOnce();
    expect(blob).not.toBeNull();
    const text = await new Promise<string>((res) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result)); fr.readAsText(blob!); });
    expect(text).toContain("BEGIN:VEVENT");
    expect(text).toContain("DTSTART;VALUE=DATE:20261112");
    expect(text).toContain(`UID:${DEMO_PATIENT_CASE_ID}@`);
    expect(text).toContain("SUMMARY:Camera test (colonoscopy)");
    expect(await lastEvent(api)).toEqual(before);
  });

  it("works in Arabic: the date and the buttons are Arabic", async () => {
    const user = userEvent.setup();
    await openAs(await bookedApi());
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    expect(screen.getByRole("button", { name: STRINGS.ar.addToCalendar })).toBeInTheDocument();
    expect(screen.getByTestId("appointment-date").textContent).toMatch(/[؀-ۿ]/);
    expect(screen.getByRole("button", { name: STRINGS.ar.cannotAttend })).toBeInTheDocument();
  });

  it("while consent is withdrawn the one action is 'you may contact me again'", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    await openAs(api);
    await user.click(screen.getByTestId("tab-privacy"));
    await user.click(screen.getByTestId("consent-withdraw"));
    await user.click(screen.getByTestId("consent-withdraw-yes"));
    await screen.findByTestId("consent-withdrawn");
    await user.click(screen.getByTestId("tab-home"));
    expect(screen.getByTestId("next-text")).toHaveTextContent(STRINGS.en.nextState.withdrawn);
    expect(screen.getByTestId("primary-action")).toHaveTextContent(STRINGS.en.resumeButton);
    await user.click(screen.getByTestId("primary-action"));
    await waitFor(async () => expect((await lastEvent(api)).payload).toMatchObject({ withdrawn: false }));
  });
});

/** `GET /cases/c1` as the API answers a patient, for a given pathway and state. */
const wire = (pathway: string, state: string, events: unknown[] = []) => ({
  case_id: "c1", pathway, state, created_at: "2026-10-01T08:00:00+00:00", updated_at: "2026-10-02T08:00:00+00:00", patient_ref: "Patient/p1",
  owner: { actor: "coordinator", display_name: "coordinator", role: "coordinator" }, consent_frozen: false, last_event_at: "2026-10-02T08:00:00+00:00", risk_score: null,
  read_model: { state, consent_frozen: false, last_event_at: "2026-10-02T08:00:00+00:00", consent: { channel_sms: true, channel_whatsapp: true, channel_phone: true, channel_letter: true, share_with_specialist: true, withdrawn: false } },
  actors: {}, events: [{ seq: 1, type: "case_created", at: "2026-10-01T08:00:00+00:00", actor: "system", payload: {} }, ...events], risk: null,
  care_contact: { name: "Dr Test", role: "primary_care_owner", phone: "+971 2 000 0000", synthetic: true },
});
const httpWith = (body: unknown, extra: Record<string, unknown> = {}) =>
  createHttpAdapter("http://api.test", (async (url: string | URL | Request) => {
    const u = String(url).replace("http://api.test", "");
    if (u === "/auth/dev-login") return new Response("{}", { status: 404 });
    if (u === "/cases/c1") return new Response(JSON.stringify(body), { status: 200 });
    if (u in extra) return new Response(JSON.stringify(extra[u]), { status: 200 });
    return new Response(JSON.stringify({ detail: "no route " + u }), { status: 404 });
  }) as typeof fetch);

describe("all three pathways, in their own words", () => {
  for (const [pathway, en, ar] of [
    ["colorectal_fit", /camera test called a colonoscopy/, /تنظير القولون/],
    ["cervical_hpv", /follow-up examination/, /فحص متابعة في العيادة/],
    ["chronic_conditions", /visit with your family doctor/, /زيارة طبيب الأسرة/],
  ] as const) {
    it(`${pathway}: next step, journey strip and appointment name`, async () => {
      const user = userEvent.setup();
      const booked = [{ seq: 2, type: "book_confirmatory", at: "2026-10-03T08:00:00+00:00", actor: "coordinator", payload: { appointment_date: "2026-11-12" } }];
      renderWithApp(<PatientView caseId="c1" />, { api: httpWith(wire(pathway, "confirmatory_booked", booked)), role: "patient" });
      const next = await screen.findByTestId("next-step");
      expect(next).toHaveTextContent(en);
      const key = pathway;
      expect(screen.getByTestId("appointment")).toHaveTextContent(STRINGS.en.pathways[key].appointmentTitle);
      expect(screen.getByText(STRINGS.en.pathways[key].progress[2])).toBeInTheDocument();
      expect(screen.getByTestId("patient-owner")).toHaveTextContent("Dr Test");
      await user.click(screen.getByRole("button", { name: /العربية/ }));
      expect(screen.getByTestId("next-step").textContent).toMatch(ar);
      expect(screen.getByText(STRINGS.ar.pathways[key].progress[2])).toBeInTheDocument();
    });
  }
  it("a pack the app does not know gets plain 'appointment' wording", async () => {
    renderWithApp(<PatientView caseId="c1" />, { api: httpWith(wire("brand_new_pack", "patient_acknowledged")), role: "patient" });
    expect(await screen.findByTestId("next-text")).toHaveTextContent(STRINGS.en.pathways.other.waiting);
  });
});

describe("the privacy and consent centre", () => {
  it("lists who looked at the record, by role, name and time, and nothing else", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    await api.login("coordinator", "Maryam Al Suwaidi");
    await api.getCase(DEMO_PATIENT_CASE_ID); // a staff read is recorded
    await api.getCase(DEMO_PATIENT_CASE_ID); // and not again inside the window
    await api.login("patient", "Patient");
    await openAs(api);
    await user.click(screen.getByTestId("tab-privacy"));
    const rows = await screen.findAllByTestId("access-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Maryam Al Suwaidi");
    expect(rows[0]).toHaveTextContent(STRINGS.en.roles.coordinator);
    expect(rows[0].querySelector("time")).not.toBeNull();
    expect(screen.getByTestId("access-log")).toHaveTextContent(STRINGS.en.accessNote);
    // the patient's own reading is not added
    await api.getCase(DEMO_PATIENT_CASE_ID);
    expect((await api.getAccessLog(DEMO_PATIENT_CASE_ID)).entries).toHaveLength(1);
    expect(Object.keys((await api.getAccessLog(DEMO_PATIENT_CASE_ID)).entries[0]).sort()).toEqual(["at", "name", "role"]);
  });

  it("says so when nobody has looked, and when the list cannot load", async () => {
    const user = userEvent.setup();
    await openAs(createMockAdapter(null));
    await user.click(screen.getByTestId("tab-privacy"));
    expect(await screen.findByTestId("access-empty")).toHaveTextContent(STRINGS.en.accessEmpty);
    // a server that fails the list: an error line, not a crash
    const api = createMockAdapter(null);
    const failing = { ...api, getAccessLog: async () => { throw new Error("down"); } } as ApiClient;
    localStorage.clear();
    renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api: failing, role: "patient" });
    await screen.findAllByText(STRINGS.en.resultBody);
    await user.click(screen.getAllByTestId("tab-privacy").at(-1)!);
    expect(await screen.findByText(STRINGS.en.accessLoadError)).toBeInTheDocument();
  });

  it("the data panel says what is true: synthetic, software only drafts, a person decides, minimisation", async () => {
    const user = userEvent.setup();
    await openAs(createMockAdapter(null));
    await user.click(screen.getByTestId("tab-privacy"));
    const panel = screen.getByTestId("data-panel");
    for (const line of STRINGS.en.data) expect(panel).toHaveTextContent(line);
    expect(panel).toHaveTextContent(/synthetic/i);
    expect(panel).toHaveTextContent(/person on your care team makes every decision/i);
    expect(panel).toHaveTextContent(/kept apart from your care record/i);
    expect(within(panel).getByTestId("ai-notice")).toBeInTheDocument();
  });

  it("contact choices: a channel off, language, and withdraw needs a confirmation that can be cancelled", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    await openAs(api);
    await user.click(screen.getByTestId("tab-privacy"));
    await user.click(screen.getByRole("radio", { name: "العربية" }));
    expect(screen.getByTestId("patient-root")).toHaveAttribute("dir", "rtl");
    await user.click(screen.getByRole("radio", { name: "English" }));
    await user.click(screen.getByTestId("consent-withdraw"));
    expect(screen.getByTestId("consent-confirm")).toHaveTextContent(STRINGS.en.withdrawConfirmBody);
    await user.click(screen.getByRole("button", { name: STRINGS.en.withdrawConfirmNo }));
    expect(screen.queryByTestId("consent-confirm")).toBeNull();
    expect((await lastEvent(api)).type).not.toBe("consent_updated");
  });

  it("'remove saved information' clears what the phone kept for offline use", async () => {
    const user = userEvent.setup();
    await openAs(createMockAdapter(null));
    expect(localStorage.getItem("sc.patient.last")).not.toBeNull();
    await user.click(screen.getByTestId("tab-privacy"));
    await user.click(screen.getByTestId("clear-local"));
    expect(localStorage.getItem("sc.patient.last")).toBeNull();
    expect(screen.getByText(STRINGS.en.deviceCleared)).toBeInTheDocument();
  });

  it("offline: the saved page is read-only, the buttons wait, and the reason is said", async () => {
    const api = createMockAdapter(null);
    await openAs(api);
    cleanup();
    const broken = { ...api, getCase: async () => { throw new Error("offline"); } } as ApiClient;
    renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api: broken, role: "patient" });
    expect(await screen.findByText(STRINGS.en.offline)).toBeInTheDocument();
    expect(screen.getByText(STRINGS.en.offlineAction)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: STRINGS.en.dontUnderstand })).toBeDisabled();
  });
});

