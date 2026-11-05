/** Guided demo for judges (item 8): role cards on the sign-in page, the five-step tour, the banner. Demo-only, secret-free. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { readFileSync } from "node:fs";
import type { ApiClient } from "../src/api/contract";
import type { OidcClient } from "../src/api/oidc";
import { createMockAdapter } from "../src/api/mockAdapter";
import { JUDGE_ROLES } from "../src/api/identities";
import { AppProvider } from "../src/app/AppContext";
import App, { SignInPage } from "../src/App";
import { CHALLENGE_STEPS, TODAY_ROUTE, PRIVACY_ROUTE } from "../src/judge/JudgeTour";
import { Header } from "../src/components/Header";

function oidcApi(demo: { simulated_clock: boolean; deployment?: boolean } | undefined, signedIn = false) {
  const signIn = vi.fn(async () => undefined);
  const oidc = { config: {}, signedIn: () => signedIn, signIn, handleRedirect: async () => false, accessToken: async () => null, signOut: () => undefined, subscribe: () => () => undefined } as unknown as OidcClient;
  const base = createMockAdapter(null);
  const api = { ...base, resetDemo: undefined, oidc, health: async () => ({ status: "ok", sim_time: "2026-01-05T08:00:00Z", ...(demo ? { demo } : {}) }) } as unknown as ApiClient;
  return { api, signIn };
}

const renderSignIn = (api: ApiClient) =>
  render(<MemoryRouter><AppProvider api={api}><SignInPage /></AppProvider></MemoryRouter>);

describe("role cards on the hosted demo's sign-in page", () => {
  it("show four roles and each starts the real sign-in with that username pre-filled", async () => {
    const { api, signIn } = oidcApi({ simulated_clock: true, deployment: true });
    renderSignIn(api);
    const cards = await screen.findByTestId("role-cards");
    expect(cards.querySelectorAll("li")).toHaveLength(4);
    expect(screen.getByTestId("demo-clock-banner").textContent).toContain("Demo, synthetic data, simulated time");
    expect(screen.getByTestId("judge-signin").textContent).toMatch(/password for each role is in the submission/);
    fireEvent.click(screen.getByRole("button", { name: /sign in as supervisor/i }));
    expect(signIn).toHaveBeenCalledWith({ loginHint: "noura.alhammadi", forceLogin: true });
  });

  it("never appear when the server does not report a demo deployment", async () => {
    for (const demo of [undefined, { simulated_clock: true }]) {
      const { api } = oidcApi(demo);
      const { unmount } = renderSignIn(api);
      await screen.findByRole("heading", { name: "Sign in" });
      await waitFor(() => expect(screen.queryByTestId("role-cards")).toBeNull());
      unmount();
    }
  });

  it("carry usernames and descriptions only: no password or secret field in the page's data", () => {
    for (const r of JUDGE_ROLES) expect(Object.keys(r).sort()).toEqual(["does", "role", "title", "tryThis", "username"]);
    const src = readFileSync("src/api/identities.ts", "utf8") + readFileSync("src/App.tsx", "utf8");
    expect(src).not.toMatch(/password\s*[:=]\s*["'`]/i);
    expect(src).not.toMatch(/VITE_[A-Z_]*(PASSWORD|SECRET)/);
  });
});

describe("the five-step tour", () => {
  it("is the challenge owner's loop, deep-linked into the real screens, with try-it prompts", () => {
    expect(CHALLENGE_STEPS.map((s) => s.id)).toEqual(["record", "prioritise", "refer", "remind", "outcome", "safety", "privacy"]);
    expect(TODAY_ROUTE).toBe("/coordinator/today");
    expect(CHALLENGE_STEPS[0].route([], null)).toBe(TODAY_ROUTE); // no fresh case known: the screen, not a case
    expect(CHALLENGE_STEPS[3].also?.map((a) => a.to)).toEqual(["/patient"]);
    expect(CHALLENGE_STEPS[6].also?.map((a) => a.to)).toEqual([PRIVACY_ROUTE]);
    expect(CHALLENGE_STEPS[4].route([], null)).toBe("/dashboard");
    expect(CHALLENGE_STEPS[4].target).toContain("funnel-card");
    for (const s of CHALLENGE_STEPS) {
      expect((s.tryIt ?? []).length).toBeGreaterThan(0);
      expect(s.claim, s.id).toBeTruthy(); // one line the step proves
      expect((s.lookFor ?? []).length, s.id).toBeGreaterThan(0); // and what to look for on the destination
    }
    expect(CHALLENGE_STEPS[5].target).toContain("missed-followup");
    expect(CHALLENGE_STEPS[5].lookFor!.join(" ")).toMatch(/accept/i);
    // about five minutes in all
    const seconds = CHALLENGE_STEPS.reduce((n, s) => n + (s.seconds ?? 0), 0);
    expect(seconds).toBeGreaterThanOrEqual(240);
    expect(seconds).toBeLessThanOrEqual(420);
  });

  it("opens on the guided steps in the demo deployment, offers the role switch, and can swap to the story", async () => {
    const base = createMockAdapter(null);
    const api = { ...base, health: async () => ({ status: "ok", sim_time: "2026-01-05T08:00:00Z", demo: { simulated_clock: true, deployment: true } }) } as ApiClient;
    render(<MemoryRouter initialEntries={["/coordinator"]}><AppProvider api={api} initialRole="coordinator"><App /></AppProvider></MemoryRouter>);
    await screen.findByTestId("demo-clock-banner");
    fireEvent.click(screen.getByTestId("judge-tour-button"));
    expect(await screen.findByTestId("tour-progress")).toHaveTextContent("Step 1 of 7");
    expect(screen.getByTestId("tour-try")).toHaveTextContent("Try it");
    fireEvent.click(screen.getByTestId("tour-next")); fireEvent.click(screen.getByTestId("tour-next"));
    expect(screen.getByTestId("tour-role-hint")).toHaveTextContent("clinician"); // step 3 is clearest as the clinician
    fireEvent.click(screen.getByTestId("tour-switch-role"));
    await waitFor(() => expect(screen.queryByTestId("tour-role-hint")).toBeNull());
    fireEvent.click(screen.getByTestId("track-story"));
    expect(screen.getByTestId("tour-progress")).toHaveTextContent("Step 1 of 7");
  });
});

describe("outside the demo", () => {
  it("the http adapter never rewinds the clock or runs the operator script: the web only REQUESTS a reset (ADR-058), the host acts", () => {
    expect(readFileSync("src/api/httpAdapter.ts", "utf8")).not.toMatch(/resetDemo|demo-reset\.sh|\/clock\/reset/);
  });

  it("shows no banner and no demo reset in the header", async () => {
    const { api } = oidcApi(undefined, true);
    render(<MemoryRouter><AppProvider api={api} initialRole="supervisor"><Header /></AppProvider></MemoryRouter>);
    await screen.findByText(/Sign out/);
    expect(screen.queryByTestId("demo-clock-banner")).toBeNull();
    expect(screen.queryByRole("button", { name: /reset demo/i })).toBeNull();
  });
});
