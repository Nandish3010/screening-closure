/** The pieces that exist because of the real API (E8): patient_ref for a patient login, actor strings, display names,
 *  the confirmatory-result route, the clock control, the signer, and the states the page used to skip. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createMockAdapter } from "../src/api/mockAdapter";
import { actorName, registerActors } from "../src/lib/derive";
import { SignOffPanel } from "../src/components/SignOffPanel";
import { Header } from "../src/components/Header";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { renderWithApp } from "./helpers";

function recorder(routes: Record<string, unknown | ((body: any) => unknown)>) {
  const calls: { key: string; body: any; auth: string | null }[] = [];
  const fn = (async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url).replace("http://api.test", "")}`;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ key, body, auth: ((init?.headers ?? {}) as Record<string, string>).Authorization ?? null });
    const r = routes[key];
    if (r === undefined) return new Response(JSON.stringify({ detail: `no route ${key}` }), { status: 404 });
    return new Response(JSON.stringify(typeof r === "function" ? (r as (b: unknown) => unknown)(body) : r), { status: 200 });
  }) as unknown as typeof fetch;
  return { fn, calls };
}

describe("http adapter against the real API's identity rules", () => {
  it("logs a patient in with the patient_ref it reads from the case with its own staff login, and sends it only for patients", async () => {
    const { fn, calls } = recorder({
      "POST /auth/dev-login": (b: any) => ({ token: b.role === "patient" ? "patient-token" : "staff-token", user_id: "u1", role: b.role }),
      "GET /cases/c1": { case_id: "c1", state: "patient_contacted", patient_ref: "Patient/42", events: [] },
      "GET /cases?limit=500": [],
    });
    const api = createHttpAdapter("http://api.test", fn);
    await api.loginPatientFor!("c1");
    await api.listCases();
    const logins = calls.filter((c) => c.key === "POST /auth/dev-login").map((c) => c.body);
    expect(logins[0]).toEqual({ role: "coordinator", name: "Maryam Al Suwaidi" }); // the lookup login: no patient_ref
    expect(logins[1]).toEqual({ role: "patient", name: "Patient", patient_ref: "Patient/42" });
    expect(calls.find((c) => c.key === "GET /cases/c1")!.auth).toBe("Bearer staff-token");
    expect(calls.find((c) => c.key === "GET /cases?limit=500")!.auth).toBe("Bearer patient-token"); // the session is the patient now
  });

  it("a patient login without a patient_ref is skipped instead of failing (the page supplies it)", async () => {
    const { fn, calls } = recorder({ "GET /cases?limit=500": [] });
    const api = createHttpAdapter("http://api.test", fn);
    await api.login("patient", "Patient");
    expect(calls).toHaveLength(0);
  });

  it("actorFor learns the opaque user id by logging in, without changing the session", async () => {
    const { fn, calls } = recorder({
      "POST /auth/dev-login": (b: any) => ({ token: `t-${b.name}`, user_id: "a1b2c3d4e5f6", role: b.role }),
      "GET /cases?limit=500": [],
    });
    const api = createHttpAdapter("http://api.test", fn);
    await api.login("coordinator", "Maryam Al Suwaidi");
    const actor = await api.actorFor("specialist", "Dr. Karim Nasser", "Corniche Gastro Unit");
    expect(actor).toBe("specialist_owner:a1b2c3d4e5f6");
    expect(calls.at(-1)!.body).toEqual({ role: "specialist_owner", name: "Dr. Karim Nasser", provider: "Corniche Gastro Unit" });
    await api.listCases();
    expect(calls.at(-1)!.auth).toBe("Bearer t-Maryam Al Suwaidi");
  });

  it("recordConfirmatoryResult and advanceClock use the new routes", async () => {
    const { fn, calls } = recorder({
      "POST /auth/dev-login": { token: "t", user_id: "u", role: "supervisor" },
      "POST /cases/c1/confirmatory-result": { case_id: "c1", evidence_ref: "Procedure/colonoscopy-c1" },
      "POST /clock/advance": { sim_time: "2026-02-01T08:00:00+00:00" },
    });
    const api = createHttpAdapter("http://api.test", fn);
    expect((await api.recordConfirmatoryResult("c1")).evidence_ref).toBe("Procedure/colonoscopy-c1");
    expect(await api.advanceClock!({ days: 7 })).toBe("2026-02-01T08:00:00+00:00");
    expect(calls.find((c) => c.key === "POST /clock/advance")!.body).toEqual({ days: 7 });
  });

  it("postReply posts the reply to POST /cases/{id}/replies (D13) and returns the API's classification", async () => {
    const answer = { reply_id: "r1", case_id: "c1", channel: "whatsapp", intent: "other", confidence: 0, model_id: "unavailable", needs_human: true, logged: true, applied: ["reply_received", "reply_classified", "review_requested"], note: null };
    const { fn, calls } = recorder({
      "POST /auth/dev-login": { token: "t", user_id: "u", role: "coordinator" },
      "POST /cases/c1/replies": answer,
    });
    const api = createHttpAdapter("http://api.test", fn);
    expect(await api.postReply("c1", { channel: "whatsapp", text: "ان شاء الله" })).toEqual(answer);
    expect(calls.find((c) => c.key === "POST /cases/c1/replies")!.body).toEqual({ channel: "whatsapp", text: "ان شاء الله" });
  });

  it("shows the display names the API resolved for opaque actor ids", () => {
    expect(actorName("primary_care_owner:deadbeef0001")).toBe("deadbeef0001");
    registerActors({ "primary_care_owner:deadbeef0001": { display_name: "Dr. Layla Haddad" } });
    expect(actorName("primary_care_owner:deadbeef0001")).toBe("Dr. Layla Haddad");
  });
});

describe("sign-off against the real API", () => {
  it("uses the caller's own Practitioner as a read-only signer and the recorded Procedure as evidence", async () => {
    const user = userEvent.setup();
    const onSignOff = vi.fn();
    const record = vi.fn(async () => "Procedure/colonoscopy-c1");
    renderWithApp(<SignOffPanel role="specialist" onSignOff={onSignOff} signer="Practitioner/abc123" onRecordResult={record} />);
    expect(screen.getByTestId("signer")).toHaveValue("Practitioner/abc123");
    expect(screen.getByTestId("signer")).toHaveAttribute("readonly");
    expect(screen.getByTestId("signoff")).toBeDisabled(); // no evidence yet
    await user.click(screen.getByTestId("record-colonoscopy"));
    await waitFor(() => expect(screen.getByLabelText("Evidence reference")).toHaveValue("Procedure/colonoscopy-c1"));
    await user.click(screen.getByTestId("signoff"));
    expect(onSignOff).toHaveBeenCalledWith("Procedure/colonoscopy-c1", "Practitioner/abc123");
  });

  it("starts from the evidence the confirmatory result already named", () => {
    renderWithApp(<SignOffPanel role="specialist" onSignOff={() => undefined} signer="Practitioner/abc123" defaultEvidence="Procedure/colonoscopy-c9" />);
    expect(screen.getByLabelText("Evidence reference")).toHaveValue("Procedure/colonoscopy-c9");
    expect(screen.getByTestId("signoff")).toBeEnabled();
  });

  it("says why when HAPI refuses to record the colonoscopy", async () => {
    const user = userEvent.setup();
    renderWithApp(<SignOffPanel role="specialist" onSignOff={() => undefined} signer="Practitioner/a" onRecordResult={async () => { throw new Error("cannot record the colonoscopy result: HAPI unreachable"); }} />);
    await user.click(screen.getByTestId("record-colonoscopy"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/HAPI unreachable/);
  });
});

describe("case page states the real pathway has", () => {
  const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;

  it("offers 'Record patient contacted' again from lost_contact (lost_contact --contact_patient--> patient_contacted)", async () => {
    // The real API moves the case to `lost_contact`; the mock keeps mark_lost_contact an annotation, so answer as the API does.
    const mock = createMockAdapter(null);
    await mock.postEvent(cid(2), { type: "mark_lost_contact", actor: "coordinator:Maryam Al Suwaidi", payload: { failed_attempts: 3 } });
    const api = { ...mock, getCase: async (id: string) => ({ ...(await mock.getCase(id)), state: "lost_contact" }) };
    renderWithApp(<CaseDetailPage />, { api, role: "coordinator", route: `/coordinator/${cid(2)}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("lost-contact-banner")).toBeInTheDocument();
    expect(screen.getByTestId("contact-patient")).toBeEnabled();
  });

  it("sends the failed attempts the pathway needs with mark_lost_contact", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    const post = vi.spyOn(api, "postEvent");
    renderWithApp(<CaseDetailPage />, { api, role: "coordinator", route: `/coordinator/${cid(2)}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("mark-lost-contact");
    await user.click(screen.getByTestId("mark-lost-contact"));
    await waitFor(() => expect(post).toHaveBeenCalledWith(cid(2), expect.objectContaining({ type: "mark_lost_contact", payload: { failed_attempts: 3 } })));
  });

  it("accept ownership sends no owner and nothing else (the API takes the caller)", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    const post = vi.spyOn(api, "postEvent");
    renderWithApp(<CaseDetailPage />, { api, role: "owner", route: `/coordinator/${cid(5)}`, path: "/coordinator/:caseId" }); // case 5 is `acknowledged`
    await user.click(await screen.findByTestId("accept-ownership"));
    await waitFor(() => expect(post).toHaveBeenCalledWith(cid(5), expect.objectContaining({ type: "accept_ownership", payload: {} })));
  });
});

describe("supervisor clock control", () => {
  it("shows an Advance clock control to a supervisor only when the adapter can move the clock", async () => {
    const user = userEvent.setup();
    const api = { ...createMockAdapter(null), advanceClock: vi.fn(async () => "2026-02-01T08:00:00+00:00") };
    renderWithApp(<Header />, { api, role: "supervisor" });
    await user.clear(screen.getByLabelText("Days to advance the simulation clock"));
    await user.type(screen.getByLabelText("Days to advance the simulation clock"), "10");
    await user.click(screen.getByTestId("advance-clock"));
    expect(api.advanceClock).toHaveBeenCalledWith({ days: 10 });
  });

  it("is absent for the mock adapter and for other roles", () => {
    renderWithApp(<Header />, { role: "supervisor" });
    expect(screen.queryByTestId("clock-control")).toBeNull();
    const api = { ...createMockAdapter(null), advanceClock: async () => "x" };
    renderWithApp(<Header />, { api, role: "coordinator" });
    expect(screen.queryByTestId("clock-control")).toBeNull();
  });
});
