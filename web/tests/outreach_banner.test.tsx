/**
 * A26 (ADR-037): the outreach policy is unsigned and the compose demo runs the automatic messages under a placeholder
 * approval. `GET /health` reports `outreach.demo_approval`; the web shows a persistent amber banner with the API's text on the
 * case page and on the dashboard, and shows nothing when the policy is really signed or the server says nothing.
 */
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ApiClient, OutreachStatus } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import { DashboardPage } from "../src/pages/DashboardPage";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { renderWithApp } from "./helpers";

const DEMO = "DEMO PLACEHOLDER (clinical sign-off pending, H1)";
const withHealth = (api: ApiClient, outreach: OutreachStatus | undefined): ApiClient =>
  ({ ...api, health: async () => ({ status: "ok", sim_time: "2026-11-03T09:00:00Z", ...(outreach ? { outreach } : {}) }) }) as ApiClient;

describe("the demo-approval banner", () => {
  it("shows the API's own text on the dashboard while outreach.demo_approval is true", async () => {
    renderWithApp(<DashboardPage />, { api: createMockAdapter(null, { automation: true }) });
    const banner = await screen.findByTestId("outreach-demo-banner");
    expect(screen.getByTestId("outreach-demo-text").textContent).toBe(DEMO);
    expect(banner.className).toContain("banner-warn"); // amber
    expect(banner.textContent).toMatch(/not on a clinical lead's signature/);
  });

  it("shows it on the case page too", async () => {
    const api = createMockAdapter(null, { automation: true });
    const id = (await api.listCases())[0].case_id;
    renderWithApp(<CaseDetailPage />, { api, role: "coordinator", route: `/coordinator/${id}`, path: "/coordinator/:caseId" });
    expect((await screen.findByTestId("outreach-demo-text")).textContent).toBe(DEMO);
  });

  it("shows whatever text the API sends", async () => {
    const api = withHealth(createMockAdapter(null), { signed: false, demo_approval: true, approved_by: "DEMO PLACEHOLDER (test)" });
    renderWithApp(<DashboardPage />, { api });
    expect((await screen.findByTestId("outreach-demo-text")).textContent).toBe("DEMO PLACEHOLDER (test)");
  });

  it("shows nothing when the policy is signed, unsigned without the demo switch, or the server does not report it", async () => {
    for (const outreach of [
      { signed: true, demo_approval: false, approved_by: "Dr. Clinical Lead" },
      { signed: false, demo_approval: false, approved_by: null },
      undefined,
    ] as (OutreachStatus | undefined)[]) {
      const { unmount } = renderWithApp(<DashboardPage />, { api: withHealth(createMockAdapter(null), outreach) });
      await screen.findByText("Closure dashboard");
      await waitFor(() => expect(screen.queryByTestId("outreach-demo-banner")).toBeNull());
      unmount();
    }
  });

  it("the manual-flow mock reports no automatic sending and no banner", async () => {
    const api = createMockAdapter(null);
    expect((await api.health()).outreach).toMatchObject({ demo_approval: false, automatic_sending: false });
  });
});
