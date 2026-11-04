import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { createMockAdapter, REROUTE_LIST } from "../src/api/mockAdapter";
import { ROLE_PROVIDERS } from "../src/api/identities";
import { caseIdFor } from "../src/api/fixtures";
import { insuranceReason, networkOf, routeNext } from "../src/lib/insurance";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

/** Insurance-aware referral routing (ADR-054) on the web: the plan on the case page, the plain reason, the out-of-network flag. */

const SARA = "primary_care_owner:Sara Al Mansoori";
const CORNICHE = "Corniche Gastro Unit", AL_NOOR = "Al Noor Endoscopy Centre", MARINA = "Marina Digestive Health";
const types = (events: { type: string }[]) => events.map((e) => e.type);
/** Case 2: owned by Sara Al Mansoori, an Enhanced plan (Marina Digestive Health does not accept it, in the synthetic networks). */
const ENHANCED_CASE = caseIdFor(2);
/** The patient acknowledges, so the case is at the step where a referral can be requested (and, if declined, re-routed). */
const acknowledged = async (api: ReturnType<typeof createMockAdapter>) => { await api.postEvent(ENHANCED_CASE, { type: "patient_acknowledge", actor: "patient:x", payload: { lang: "en" } }); };

describe("the routing rule the mock mirrors", () => {
  it("prefers the first provider in network, falls back to the first one out of it, and never returns nothing while one is left", () => {
    expect(routeNext(REROUTE_LIST, "enhanced", new Set())).toEqual({ provider: CORNICHE, network: "in_network" });
    expect(routeNext(REROUTE_LIST, "enhanced", new Set([CORNICHE]))).toEqual({ provider: AL_NOOR, network: "in_network" });
    expect(routeNext(REROUTE_LIST, "enhanced", new Set([CORNICHE, AL_NOOR]))).toEqual({ provider: MARINA, network: "out_of_network" });
    expect(routeNext(REROUTE_LIST, "enhanced", new Set(REROUTE_LIST))).toBeNull();
  });
  it("treats no scheme and unknown as today: the order alone, no network", () => {
    for (const scheme of [undefined, null, "unknown", "gold"]) {
      expect(routeNext(REROUTE_LIST, scheme, new Set([CORNICHE]))).toEqual({ provider: AL_NOOR, network: null });
      expect(networkOf(MARINA, scheme)).toBeNull();
    }
  });
  it("says why in plain words and says nothing when the scheme took no part", () => {
    expect(insuranceReason("basic", "in_network", true)).toBe("Chosen because it accepts the patient's Basic plan and is the first such unit in the programme's referral order.");
    expect(insuranceReason("basic", "in_network", false)).toBe("This unit accepts the patient's Basic plan.");
    expect(insuranceReason("enhanced", "out_of_network", true)).toMatch(/^Out of network: no remaining unit .* Enhanced plan.*not blocked\.$/);
    expect(insuranceReason("unknown", null, true)).toBeNull();
  });
});

describe("the mock adapter", () => {
  it("shows staff a plan on the seeded bowel cases, a mix of schemes, and never shows one to a patient", async () => {
    const api = createMockAdapter(null, { automation: true });
    const rows = await api.listCases();
    const schemes = new Set(rows.map((r) => r.insurance?.scheme));
    expect([...schemes].sort()).toEqual(["basic", "enhanced", "thiqa", "unknown"]);
    expect(rows.every((r) => r.pathway === "colorectal_fit")).toBe(true);
    await api.login("patient", "Aisha", ROLE_PROVIDERS.patient);
    expect((await api.listCases()).every((r) => !("insurance" in r))).toBe(true);
    expect("insurance" in (await api.getCase(caseIdFor(13)))).toBe(false);
  });
  it("records the network of a referral on the server's side and replaces a value the client sends", async () => {
    const api = createMockAdapter(null, { automation: true });
    await acknowledged(api);
    const d = await api.postEvent(ENHANCED_CASE, { type: "handoff_requested", actor: SARA, payload: { to_provider: MARINA, network: "in_network" } });
    const req = d.events.find((e) => e.type === "handoff_requested")!;
    expect(req.payload).toMatchObject({ to_provider: MARINA, network: "out_of_network" });
    expect(d.insurance?.referral).toMatchObject({ provider: MARINA, network: "out_of_network", out_of_network: true, auto: false });
    expect(d.insurance?.referral?.reason).toContain("this unit does not accept the patient's Enhanced plan");
  });
  it("re-routes in network first and, with none left, to the best out-of-network unit, flagged and not blocked", async () => {
    const api = createMockAdapter(null, { automation: true, decliningProviders: [CORNICHE, AL_NOOR] });
    await acknowledged(api);
    const d = await api.postEvent(ENHANCED_CASE, { type: "handoff_requested", actor: SARA, payload: { to_provider: CORNICHE } });
    const asked = d.events.filter((e) => e.type === "handoff_requested").map((e) => [e.payload.to_provider, e.payload.network]);
    expect(asked).toEqual([[CORNICHE, "in_network"], [AL_NOOR, "in_network"], [MARINA, "out_of_network"]]);
    expect(d.state).toBe("owner_accepted"); // Marina accepted: the referral was never blocked
    expect(d.insurance?.referral).toMatchObject({ provider: MARINA, out_of_network: true, auto: true });
  });
  it("leaves a case with an unknown plan to the order alone", async () => {
    const api = createMockAdapter(null, { automation: true });
    const d = await api.postEvent(caseIdFor(8), { type: "handoff_requested", actor: "primary_care_owner:Omar Khalid", payload: { to_provider: AL_NOOR } });
    const asked = d.events.filter((e) => e.type === "handoff_requested");
    expect(asked.map((e) => e.payload.to_provider)).toEqual([AL_NOOR, REROUTE_LIST[0]]);
    expect(asked.every((e) => !("network" in e.payload))).toBe(true);
    expect(d.insurance).toMatchObject({ scheme: "unknown", referral: { network: null, reason: null } });
  });
});

describe("the case page and the worklist", () => {
  it("shows the plan and, after a referral in network, why this provider", async () => {
    const api = createMockAdapter(null, { automation: true, decliningProviders: [] });
    await acknowledged(api);
    await api.postEvent(ENHANCED_CASE, { type: "handoff_requested", actor: SARA, payload: { to_provider: AL_NOOR } });
    await api.login("coordinator", "Maryam Al Suwaidi");
    renderWithApp(<CaseDetailPage />, { api, route: `/coordinator/${ENHANCED_CASE}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("insurance-scheme")).toHaveTextContent("Enhanced plan");
    expect(screen.getByTestId("referral-why")).toHaveTextContent(`Why ${AL_NOOR}`);
    expect(screen.getByTestId("referral-reason")).toHaveTextContent("This unit accepts the patient's Enhanced plan.");
    expect(screen.queryByTestId("out-of-network-chip")).toBeNull();
    expect(screen.getByTestId("insurance-note")).toHaveTextContent("Synthetic");
  });
  it("flags an out-of-network referral on the case page and for the coordinator in the worklist", async () => {
    const api = createMockAdapter(null, { automation: true, decliningProviders: [CORNICHE, AL_NOOR] });
    await acknowledged(api);
    await api.postEvent(ENHANCED_CASE, { type: "handoff_requested", actor: SARA, payload: { to_provider: CORNICHE } });
    await api.login("coordinator", "Maryam Al Suwaidi");
    const page = renderWithApp(<CaseDetailPage />, { api, route: `/coordinator/${ENHANCED_CASE}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("out-of-network-chip")).toHaveTextContent("Out of network");
    expect(screen.getByTestId("referral-reason")).toHaveTextContent(/no remaining unit .* Enhanced plan/);
    expect(screen.getByTestId("in-network-options")).toHaveTextContent(`${CORNICHE}, ${AL_NOOR}`);
    page.unmount();
    renderWithApp(<CoordinatorPage />, { api });
    const flags = await screen.findAllByTestId("out-of-network-flag");
    expect(flags).toHaveLength(1);
    expect(types((await api.getCase(ENHANCED_CASE)).events)).toContain("handoff_requested");
  });
  it("shows nothing for a case with no scheme, and nothing at all to a patient", async () => {
    const api = createMockAdapter(null, { automation: true, thirdPathway: true });
    renderWithApp(<CaseDetailPage />, { api, route: `/coordinator/${caseIdFor(40)}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("owner");
    expect(screen.queryByTestId("insurance")).toBeNull();
  });
});
