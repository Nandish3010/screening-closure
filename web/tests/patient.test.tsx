import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DEMO_PATIENT_CASE_ID } from "../src/api/fixtures";
import { createMockAdapter } from "../src/api/mockAdapter";
import { ApiError, type ApiClient, type CaseDetail } from "../src/api/contract";
import { STRINGS } from "../src/i18n/strings";
import { PatientView } from "../src/pages/PatientPage";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { renderWithApp } from "./helpers";

/**
 * What staff see of the case. The mock redacts the case for a patient login exactly as the API does (SEC-2: staff names,
 * actors and internal events are hidden), so a test that checks the full log reads it with a staff login and switches back.
 */
const staffCase = async (api: ApiClient): Promise<CaseDetail> => {
  await api.login("coordinator", "Maryam Al Suwaidi");
  try {
    return await api.getCase(DEMO_PATIENT_CASE_ID);
  } finally {
    await api.login("patient", "Patient");
  }
};

const open = async (api = createMockAdapter(null)) => {
  renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api, role: "patient" });
  await screen.findByText(STRINGS.en.resultBody);
  return api;
};

describe("Patient page", () => {
  it("renders the plain-language result, next steps and owner", async () => {
    await open();
    expect(screen.getByText(STRINGS.en.resultBody)).toBeInTheDocument();
    expect(screen.getByText("Your care team will explain your result in person or by phone.")).toBeInTheDocument();
    // SEC-2: the patient's answer carries the owner's role only, so the page says "your primary care doctor", never a name.
    expect(screen.getByTestId("patient-owner")).toHaveTextContent(STRINGS.en.ownerByRole.primary_care_owner);
    expect(screen.getByTestId("patient-owner")).not.toHaveTextContent("Sara");
    expect(screen.getAllByRole("listitem").length).toBeGreaterThan(3);
  });

  it("E14: the page never states or hints at what the screening found, in either language", async () => {
    const user = userEvent.setup();
    await open();
    const root = screen.getByTestId("patient-root");
    const banned = [/blood/i, /cancer/i, /positive/i, /abnormal/i, /diagnos/i, /found/i, /worry/i, /\bnormal\b/i,
      /دم/, /سرطان/, /ايجابي|إيجابي/, /مصاب/, /تشخيص/, /طبيعي/, /تقلق/];
    for (const lang of ["en", "ar"] as const) {
      if (lang === "ar") await user.click(screen.getByRole("button", { name: /العربية/ }));
      const text = root.textContent ?? "";
      for (const re of banned) expect(text, `${lang}: ${re}`).not.toMatch(re);
      // The explicit line, the owner, the appointment, the acknowledge and the callback buttons.
      expect(screen.getByTestId("explain-in-person")).toHaveTextContent(STRINGS[lang].explainInPerson);
      expect(screen.getByTestId("patient-owner")).toHaveTextContent(STRINGS[lang].ownerByRole.primary_care_owner);
      expect(screen.getByRole("button", { name: STRINGS[lang].understand })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: STRINGS[lang].dontUnderstand })).toBeInTheDocument();
    }
  });

  it("E14: the string catalogue itself carries no result-disclosing or reassuring words", () => {
    const all = (lang: "en" | "ar") => JSON.stringify(Object.values(STRINGS[lang]).map((v) => (typeof v === "function" ? v("X", "Y") : v)));
    expect(all("en")).not.toMatch(/blood|cancer|positive|abnormal|diagnos|does not mean|worry/i);
    expect(all("ar")).not.toMatch(/دم|سرطان|ايجابي|إيجابي|مصاب|تشخيص|لا يعني|تقلق/);
    expect(STRINGS.en.explainInPerson).toBe("Your care team will explain your result in person or by phone.");
    expect(STRINGS.ar.explainInPerson).toMatch(/[؀-ۿ]/);
    expect(STRINGS.en.understand).toBe("I understand, please contact me");
  });

  it("Arabic toggle sets dir=rtl and lang=ar and swaps strings; toggling back restores ltr", async () => {
    const user = userEvent.setup();
    await open();
    const root = screen.getByTestId("patient-root");
    expect(root).toHaveAttribute("dir", "ltr");
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    expect(root).toHaveAttribute("dir", "rtl");
    expect(root).toHaveAttribute("lang", "ar");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(screen.getByText(STRINGS.ar.resultBody)).toBeInTheDocument();
    expect(screen.queryByText(STRINGS.en.resultBody)).toBeNull();
    expect(screen.getByRole("button", { name: STRINGS.ar.dontUnderstand })).toBeInTheDocument();
    expect(screen.getByTestId("patient-owner").textContent).toContain("طبيبك في الرعاية الأولية");
    await user.click(screen.getByRole("button", { name: /English/ }));
    expect(root).toHaveAttribute("dir", "ltr");
    expect(screen.getByText(STRINGS.en.resultBody)).toBeInTheDocument();
  });

  it('"I don\'t understand this" posts callback_requested (with the current language)', async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByRole("button", { name: STRINGS.en.dontUnderstand }));
    await screen.findByTestId("callback-done");
    const last = (await staffCase(api)).events.at(-1)!;
    expect(last.type).toBe("callback_requested");
    expect(last.actor).toBe(`patient:${DEMO_PATIENT_CASE_ID}`);
    expect(last.payload).toMatchObject({ reason: "patient_does_not_understand", lang: "en" });
  });

  it("posts the callback in Arabic with lang=ar", async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    await user.click(screen.getByRole("button", { name: STRINGS.ar.dontUnderstand }));
    await screen.findByText(STRINGS.ar.callbackDone);
    expect((await api.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)?.payload.lang).toBe("ar");
  });

  it("acknowledge posts patient_acknowledge and moves the case on", async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByRole("button", { name: STRINGS.en.understand }));
    await screen.findByTestId("ack-done");
    const d = await api.getCase(DEMO_PATIENT_CASE_ID);
    expect(d.state).toBe("patient_acknowledged");
    expect(d.events.at(-1)?.type).toBe("patient_acknowledge");
  });

  it("consent controls post partial consent_updated booleans (A6)", async () => {
    const user = userEvent.setup();
    const api = await open();
    const last = async () => (await staffCase(api)).events.at(-1)!;
    // Everything is allowed until the patient says otherwise.
    for (const c of ["Text message", "WhatsApp", "Phone call", "Letter"]) expect(screen.getByRole("checkbox", { name: c })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Text message" }));
    await waitFor(async () => expect((await last()).payload).toEqual({ channel_sms: false }));
    expect((await last()).type).toBe("consent_updated");
    await user.click(screen.getByRole("checkbox", { name: STRINGS.en.shareLabel }));
    await waitFor(async () => expect((await last()).payload).toEqual({ share_with_specialist: false }));
    const detail = await staffCase(api);
    expect(detail.consent).toMatchObject({ channel_sms: false, channel_whatsapp: true, share_with_specialist: false, withdrawn: false });
    expect(detail.consent_history).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: "Text message" })).not.toBeChecked();
  });

  it("withdrawing consent freezes outreach and flags the case for review; the patient can resume", async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByTestId("consent-withdraw"));
    await screen.findByTestId("consent-withdrawn");
    let d = await staffCase(api);
    expect(d.events.at(-2)).toMatchObject({ type: "consent_updated", payload: { withdrawn: true } });
    expect(d.events.at(-1)).toMatchObject({ type: "review_requested", actor: "system:consent", payload: { reason: "consent_withdrawn" } });
    expect(d).toMatchObject({ consent_frozen: true, review_reason: "consent_withdrawn" });
    await user.click(screen.getByTestId("consent-resume"));
    await waitFor(() => expect(screen.queryByTestId("consent-withdrawn")).toBeNull());
    d = await staffCase(api);
    expect(d).toMatchObject({ consent_frozen: false, review_reason: null });
  });

  it("the withdraw control works in Arabic and the notice is Arabic", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    await user.click(screen.getByRole("button", { name: STRINGS.ar.withdrawButton }));
    expect(await screen.findByText(STRINGS.ar.withdrawnNotice)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: STRINGS.ar.resumeButton })).toBeInTheDocument();
  });

  it("renders the last saved case when the API is unreachable", async () => {
    const api = createMockAdapter(null);
    const first = renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api, role: "patient" });
    await screen.findByText(STRINGS.en.resultBody);
    first.unmount();
    const broken = { ...api, getCase: async () => { throw new ApiError(0, "offline"); } };
    renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api: broken, role: "patient" });
    expect(await screen.findByText(STRINGS.en.offline)).toBeInTheDocument();
    expect(within(screen.getByTestId("patient-root")).getByText(STRINGS.en.resultBody)).toBeInTheDocument();
  });
});


/** `GET /cases/{id}` as the API answers a patient (api/app/readmodel.py `patient_case_json`): the owner by role only. */
const redactedWire = (owner: unknown, overrides: Record<string, unknown> = {}) => ({
  case_id: "c1", pathway: "colorectal_fit", state: "acknowledged", created_at: "2026-10-01T08:00:00+00:00",
  updated_at: "2026-10-02T08:00:00+00:00", patient_ref: "Patient/p1", owner, consent_frozen: false,
  last_event_at: "2026-10-02T08:00:00+00:00", risk_score: null,
  read_model: {
    state: "acknowledged", consent_frozen: false, last_event_at: "2026-10-02T08:00:00+00:00",
    consent: { channel_sms: true, channel_whatsapp: true, channel_phone: true, channel_letter: true, share_with_specialist: true, withdrawn: false },
  },
  actors: { coordinator: { actor: "coordinator", display_name: "coordinator", role: "coordinator" } },
  events: [
    { seq: 1, event_id: "e1", type: "case_created", at: "2026-10-01T08:00:00+00:00", actor: "system", payload: {} },
    { seq: 2, event_id: "e2", type: "acknowledge", at: "2026-10-02T08:00:00+00:00", actor: "coordinator", payload: {} },
  ],
  risk: null,
  ...overrides,
});
const roleOnly = (role: string) => ({ actor: role, display_name: role, role });

/** A real http adapter over a canned `fetch` that answers every case read with `body`. */
const httpApi = (body: unknown) => {
  const fetchImpl = (async (url: string | URL | Request) => {
    const u = String(url).replace("http://api.test", "");
    if (u === "/auth/dev-login") return new Response("{}", { status: 404 });
    if (u === "/cases/c1") return new Response(JSON.stringify(body), { status: 200 });
    return new Response(JSON.stringify({ detail: "no route " + u }), { status: 404 });
  }) as typeof fetch;
  return createHttpAdapter("http://api.test", fetchImpl);
};
const openWire = async (body: unknown) => {
  renderWithApp(<PatientView caseId="c1" />, { api: httpApi(body), role: "patient" });
  return screen.findByTestId("patient-owner");
};

describe("SEC-2: the redacted patient case", () => {
  it("the mock answers a patient with the redacted shape: owner by role, no staff names, only allow-listed events", async () => {
    const api = createMockAdapter(null);
    await api.login("patient", "Patient");
    const d = await api.getCase(DEMO_PATIENT_CASE_ID);
    expect(d).toMatchObject({ owner: "primary_care_owner", owner_actor: "primary_care_owner", owner_role: "primary_care_owner" });
    expect(JSON.stringify(d)).not.toMatch(/Layla|Sara|Karim|Huda|Maryam|Haddad|Mansoori/);
    expect(d.events.length).toBeGreaterThan(0);
    for (const e of d.events) {
      expect(e.actor).not.toContain(":"); // a role, never `role:id`
      expect(["case_created", "acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge", "book_confirmatory", "complete_confirmatory",
        "clinician_review", "close", "reopen", "consent_updated", "callback_requested", "message_sent"]).toContain(e.type);
    }
    expect(d.risk_score ?? null).toBeNull();
    const timeline = await api.getTimeline(DEMO_PATIENT_CASE_ID);
    expect(timeline.items.every((i) => i.kind === "event")).toBe(true);
    // the staff view of the same case still names the person
    await api.login("coordinator", "Maryam Al Suwaidi");
    expect((await api.getCase(DEMO_PATIENT_CASE_ID)).owner).toBe("Sara Al Mansoori");
  });

  it("renders each owner role in plain words in both languages, from the API's exact shape", async () => {
    const user = userEvent.setup();
    for (const [role, en, ar] of [
      ["primary_care_owner", "Your primary care doctor", "طبيبك في الرعاية الأولية"],
      ["specialist_owner", "Your specialist doctor", "طبيبك الأخصائي"],
      ["coordinator", "Your care coordinator", "منسق الرعاية"],
      ["supervisor", "A senior member of your care team", "أحد كبار أعضاء فريق رعايتك"],
    ] as const) {
      const view = renderWithApp(<PatientView caseId="c1" />, { api: httpApi(redactedWire(roleOnly(role))), role: "patient" });
      const owner = await screen.findByTestId("patient-owner");
      expect(owner).toHaveTextContent(en);
      expect(owner.textContent).not.toMatch(/undefined|null|primary_care_owner|specialist_owner/);
      await user.click(screen.getByRole("button", { name: /العربية/ }));
      expect(screen.getByTestId("patient-owner").textContent).toContain(ar);
      view.unmount();
      localStorage.clear();
    }
  });

  it("an unlisted or odd role falls back to 'your care team'; no owner yet shows the pending line; neither crashes", async () => {
    for (const odd of ["lab", "toString", "__proto__", "constructor", ""]) {
      const view = renderWithApp(<PatientView caseId="c1" />, { api: httpApi(redactedWire(roleOnly(odd))), role: "patient" });
      expect(await screen.findByTestId("patient-owner")).toHaveTextContent(STRINGS.en.ownerByRole.other);
      view.unmount();
      localStorage.clear();
    }
    expect(await openWire(redactedWire(null))).toHaveTextContent(STRINGS.en.ownerPending);
  });

  it("the rest of the page works on the redacted answer: result, progress, consent defaults and the buttons", async () => {
    await openWire(redactedWire(roleOnly("primary_care_owner")));
    expect(screen.getByText(STRINGS.en.resultBody)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: STRINGS.en.dontUnderstand })).toBeEnabled();
    for (const c of ["Text message", "WhatsApp", "Phone call", "Letter"]) expect(screen.getByRole("checkbox", { name: c })).toBeChecked();
    expect(screen.getByTestId("consent-withdraw")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/undefined|NaN|\[object/);
  });

  it("a staff-shaped answer (a coordinator previewing the page) still names the person", async () => {
    const staff = redactedWire(
      { actor: "primary_care_owner:u1", display_name: "Dr. Layla Haddad", role: "primary_care_owner" },
      {
        actors: { "primary_care_owner:u1": { actor: "primary_care_owner:u1", display_name: "Dr. Layla Haddad", role: "primary_care_owner" } },
        events: [{ seq: 1, type: "accept_ownership", at: "2026-10-01T08:00:00+00:00", actor: "primary_care_owner:u1", payload: {} }],
      },
    );
    const owner = await openWire(staff);
    expect(owner).toHaveTextContent("Dr. Layla Haddad");
  });
});
