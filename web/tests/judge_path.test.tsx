/** The judge path: where each role lands, what the patient never sees, the compact header, the reset confirm, the operator link, the signer's name. */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import App, { SignInPage } from "../src/App";
import { AppProvider } from "../src/app/AppContext";
import type { ApiClient, CaseSummary, Me, Role } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import type { OidcClient } from "../src/api/oidc";
import { registerActors } from "../src/lib/derive";
import { stripInternalCodes } from "../src/lib/format";
import { ClosedReceipt, SignOffPanel } from "../src/components/SignOffPanel";
import { ResetDemoControl } from "../src/components/DemoStatus";
import { renderWithApp } from "./helpers";

const DEMO = { simulated_clock: true, deployment: true, last_reset_at: "2026-11-08T06:00:00Z" };
const where = { path: "" };
function Where() { where.path = useLocation().pathname; return null; }

function oidcStub(): OidcClient {
  return { config: {}, signedIn: () => true, signIn: vi.fn(), handleRedirect: async () => false, accessToken: async () => "AT", signOut: () => undefined, adoptTokens: () => undefined, subscribe: () => () => undefined } as unknown as OidcClient;
}
const HUDA = "primary_care_owner:Dr. Huda Rahman"; // the mock adapter's actor for the physician login
const row = (id: string, state: string, ownerActor: string | null): CaseSummary => ({
  case_id: id, pathway: "colorectal_fit", state, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z", owner: ownerActor ? "Dr. Huda Rahman" : null,
  owner_actor: ownerActor, due_at: null, overdue: false, escalation_level: 0, consent_frozen: false, last_event_at: "2026-10-02T00:00:00Z",
} as CaseSummary);

/** A hosted-demo app signed in through a sign-in provider as `role`; `/me` answers after a tick, as it does against the real API. */
function hosted(role: Role, cases: CaseSummary[] = [], route = "/") {
  const base = createMockAdapter(null);
  const me: Me = { user_id: "u-huda", name: role === "clinician" ? "Dr. Huda Rahman" : "Someone", role } as Me;
  const api = {
    ...base, oidc: oidcStub(), demoSignIn: async () => undefined, requestDemoReset: async () => ({ message: "ok" }),
    me: async () => { await new Promise((r) => setTimeout(r, 20)); return me; },
    health: async () => ({ status: "ok", sim_time: "2026-11-08T09:00:00Z", demo: DEMO }),
    listCases: async () => cases,
  } as unknown as ApiClient;
  // The role starts as a stale default, as it does in a fresh tab: only /me says who signed in.
  render(<MemoryRouter initialEntries={[route]}><AppProvider api={api} initialRole="coordinator"><Where /><App /></AppProvider></MemoryRouter>);
  return api;
}

describe("where each role lands after the one-click sign-in", () => {
  it("the patient lands on /patient (not the staff guide) and sees no staff navigation and no Reset", async () => {
    hosted("patient");
    await waitFor(() => expect(where.path).toBe("/patient"));
    expect(document.querySelector('nav[aria-label="Main"]')).toBeNull();
    expect(screen.queryByTestId("judge-tour-button")).toBeNull();
    expect(screen.queryByTestId("reset-demo")).toBeNull();
    expect(screen.queryByText(/Reset the demo/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Start here" })).toBeNull();
  });

  it("a patient who opens a staff link is sent to /patient", async () => {
    hosted("patient", [], "/coordinator");
    await waitFor(() => expect(where.path).toBe("/patient"));
  });

  it("does not guess the landing page from a stale role before /me has answered", async () => {
    hosted("patient");
    expect(where.path).not.toBe("/start");
    expect(await screen.findByText("Loading...")).toBeTruthy();
  });

  it("the physician lands straight on the case that waits for her signature", async () => {
    hosted("clinician", [
      row("c-other", "owner_accepted", HUDA),
      row("c-sign", "confirmatory_completed", HUDA),
      row("c-notmine", "confirmatory_completed", "primary_care_owner:someone-else"),
    ]);
    await waitFor(() => expect(where.path).toBe("/coordinator/c-sign"));
  });

  it("without a case to sign she lands on her own page, with her cases and no dead end", async () => {
    hosted("clinician", [row("c-other", "owner_accepted", HUDA)]);
    await waitFor(() => expect(where.path).toBe("/physician"));
    expect(await screen.findByTestId("physician-none-awaiting")).toBeTruthy();
    expect(screen.getByTestId("physician-case-c-other")).toBeTruthy();
    expect(screen.getByRole("link", { name: "My cases" })).toBeTruthy();
  });

  it("/coordinator/today for a physician redirects to her page instead of saying it is not for her", async () => {
    hosted("clinician", [], "/coordinator/today");
    await waitFor(() => expect(where.path).toBe("/physician"));
    expect(document.body.textContent).not.toMatch(/Today screen is for the care coordinator/);
  });

  it("the physician page lists cases that need her signature first, each with 'Review and sign'", async () => {
    hosted("clinician", [row("c-sign", "confirmatory_completed", HUDA), row("c-sign2", "clinician_reviewed", HUDA)], "/physician");
    const first = await screen.findByTestId("physician-case-c-sign");
    expect(within(first).getByRole("link", { name: "Review and sign" }).getAttribute("href")).toBe("/coordinator/c-sign");
    expect((await screen.findByTestId("physician-case-c-sign2")).textContent).toMatch(/Review and sign/);
    await waitFor(() => expect(screen.getByRole("heading", { name: /Needs your signature \(2\)/ })).toBeTruthy());
  });

  it("staff roles still land on Start here on the hosted demo", async () => {
    hosted("supervisor");
    await waitFor(() => expect(where.path).toBe("/start"));
  });
});

describe("compact header and role labels", () => {
  it("is two short rows: brand and navigation, then the demo controls", async () => {
    hosted("supervisor", [], "/dashboard");
    await screen.findByTestId("demo-clock-banner");
    const header = document.querySelector("header.app-header")!;
    expect(header.querySelectorAll(":scope > .header-row").length).toBe(2);
    expect(header.querySelector(".header-row-main nav")).toBeTruthy();
    expect(within(header as HTMLElement).queryByText("http")).toBeNull(); // no internal adapter name on the hosted demo
  });

  it("names the physician role 'Physician' in the role picker, with no '(clinician)' suffix", async () => {
    hosted("supervisor", [], "/dashboard");
    const select = await screen.findByLabelText("Sign in as");
    const labels = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(labels).toContain("Physician (case owner)");
    expect(labels.join("|")).not.toMatch(/clinician/i);
  });

  it("the mock role picker says Physician for the clinician key", async () => {
    renderWithApp(<App />, { role: "coordinator", route: "/coordinator" });
    const select = (await screen.findByLabelText("Switch role")) as HTMLSelectElement;
    const opt = Array.from(select.options).find((o) => o.value === "clinician")!;
    expect(opt.textContent).toBe("Physician");
  });
});

describe("Reset the demo", () => {
  it("asks first, in plain words, and a different button style from Advance", async () => {
    const base = createMockAdapter(null);
    const api = { ...base, requestDemoReset: vi.fn(async () => ({ message: "ok" })), health: async () => ({ status: "ok", sim_time: "2026-11-08T09:00:00Z", demo: DEMO }) } as unknown as ApiClient;
    renderWithApp(<ResetDemoControl />, { api, role: "supervisor" });
    const open = await screen.findByTestId("reset-demo-open");
    expect(open.className).toContain("btn-danger");
    expect(open.className).not.toContain("btn-primary");
    fireEvent.click(open);
    expect(screen.getByTestId("reset-demo-confirm").textContent).toContain("This resets the shared demo for every judge. Continue?");
    expect(api.requestDemoReset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("reset-demo-cancel"));
    expect(api.requestDemoReset).not.toHaveBeenCalled();
    expect(screen.getByTestId("reset-demo-open")).toBeTruthy();
    fireEvent.click(screen.getByTestId("reset-demo-open"));
    await act(async () => { fireEvent.click(screen.getByTestId("reset-demo-yes")); });
    expect(api.requestDemoReset).toHaveBeenCalledTimes(1);
  });

  it("is not rendered at all for the patient", async () => {
    const api = { ...createMockAdapter(null), requestDemoReset: async () => ({ message: "ok" }), health: async () => ({ status: "ok", sim_time: "2026-11-08T09:00:00Z", demo: DEMO }) } as unknown as ApiClient;
    renderWithApp(<ResetDemoControl />, { api, role: "patient" });
    await waitFor(() => expect(screen.queryByTestId("reset-demo")).toBeNull());
  });
});

describe("the sign-in page's operator link", () => {
  it("is a quiet link below the role cards, labelled for operators, with no password button look", async () => {
    const oidc = oidcStub();
    const api = { ...createMockAdapter(null), oidc, demoSignIn: async () => undefined, health: async () => ({ status: "ok", sim_time: "2026-11-08T09:00:00Z", demo: DEMO }) } as unknown as ApiClient;
    render(<MemoryRouter><AppProvider api={api}><SignInPage /></AppProvider></MemoryRouter>);
    const cards = await screen.findByTestId("role-cards");
    const link = screen.getByTestId("password-signin");
    expect(link.textContent).toBe("Operator sign-in");
    expect(link.className).toContain("operator-link");
    expect(link.className).not.toMatch(/\bbtn\b/);
    expect(cards.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(link.closest("p")!.textContent).toMatch(/people who run this demo/);
  });
});

describe("physician copy", () => {
  it("the sign-off panel is headed 'Physician sign-off and closure' and shows the signer's name, with the id in a tooltip", () => {
    registerActors({ "primary_care_owner:c51f6a6637ec": { display_name: "Dr. Huda Rahman" } });
    renderWithApp(<SignOffPanel role="clinician" onSignOff={() => undefined} signer="Practitioner/c51f6a6637ec" />);
    expect(screen.getByText("Physician sign-off and closure")).toBeTruthy();
    expect(screen.queryByText(/Clinician sign-off and closure/)).toBeNull();
    const input = screen.getByTestId("signer") as HTMLInputElement;
    expect(input.value).toBe("Dr. Huda Rahman");
    expect(input.title).toBe("Practitioner/c51f6a6637ec");
    expect(screen.getByTestId("check-signer").textContent).toBe("Dr. Huda Rahman");
  });

  it("uses the name passed in for the signer when the id is not registered", () => {
    renderWithApp(<SignOffPanel role="clinician" onSignOff={() => undefined} signer="Practitioner/zz9" signerName="Dr. Layla Haddad" />);
    expect((screen.getByTestId("signer") as HTMLInputElement).value).toBe("Dr. Layla Haddad");
  });

  it("the closed receipt names the signer", () => {
    registerActors({ "primary_care_owner:abc123": { display_name: "Dr. Layla Haddad" } });
    const events = [{ type: "close", at: "2026-11-01T10:00:00Z", payload: { evidence_ref: "Procedure/x-1", signed_by: "Practitioner/abc123" } }] as never;
    render(<ClosedReceipt events={events} caseId="case-1234567" />);
    expect(screen.getByTestId("receipt-signer").textContent).toBe("Dr. Layla Haddad");
    expect(screen.getByTestId("receipt-signer").getAttribute("title")).toBe("Practitioner/abc123");
  });
});

describe("no internal codes in judge-facing copy", () => {
  it("strips tracking codes and keeps the caveat", () => {
    expect(stripInternalCodes("DEMO PLACEHOLDER (clinical sign-off pending, H1)")).toBe("DEMO PLACEHOLDER (clinical sign-off pending)");
    expect(stripInternalCodes("Arabic text pending native-speaker review (H2)")).toBe("Arabic text pending native-speaker review");
    expect(stripInternalCodes("needs clinician sign-off")).toBe("needs clinician sign-off");
  });
});

describe("the Judge tour panel", () => {
  it("can be minimised so the page gets its width back, and expanded again", async () => {
    hosted("supervisor", [], "/dashboard");
    fireEvent.click(await screen.findByTestId("judge-tour-button"));
    const panel = await screen.findByTestId("judge-tour");
    expect(panel.className).not.toContain("tour-min");
    expect(document.body.classList.contains("tour-open")).toBe(true);
    const fold = screen.getByTestId("tour-fold");
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(fold);
    expect(panel.className).toContain("tour-min");
    expect(document.body.classList.contains("tour-minimised")).toBe(true);
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(fold.textContent).toBe("Show details");
    fireEvent.click(fold);
    expect(panel.className).not.toContain("tour-min");
  });

  it("switches role with the same one-click sign-in (rate-limit wait, no password flow)", async () => {
    const { ApiError } = await import("../src/api/contract");
    const base = createMockAdapter(null);
    const oidc = oidcStub();
    const me = { user_id: "u", name: "Dr. Noura Al Hammadi", role: "supervisor" } as Me;
    const demoSignIn = vi.fn(async () => { throw new ApiError(429, "Too many sign-ins", 20, "signin_rate_limited"); });
    const api = { ...base, oidc, demoSignIn, me: async () => me, health: async () => ({ status: "ok", sim_time: "2026-11-08T09:00:00Z", demo: DEMO }), listCases: async () => [] } as unknown as ApiClient;
    render(<MemoryRouter initialEntries={["/dashboard"]}><AppProvider api={api} initialRole="supervisor"><App /></AppProvider></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("judge-tour-button"));
    fireEvent.click(await screen.findByTestId("tour-switch-role")); // step 1 is clearest as the coordinator
    expect(demoSignIn).toHaveBeenCalledWith("coordinator");
    expect((await screen.findAllByTestId("signin-limited"))[0].textContent).toMatch(/please wait 20 seconds/);
    expect(oidc.signIn).not.toHaveBeenCalled();
  });
});
