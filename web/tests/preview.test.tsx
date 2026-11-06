/** The sign-in loading state, signed-out deep links on the hosted demo, and the public /preview page (no API calls, no live data). */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { existsSync, readdirSync, statSync } from "node:fs";
import type { ApiClient } from "../src/api/contract";
import type { OidcClient } from "../src/api/oidc";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createMockAdapter } from "../src/api/mockAdapter";
import { AppProvider } from "../src/app/AppContext";
import App, { SignInPage } from "../src/App";
import { PREVIEW_STEPS } from "../src/pages/PreviewPage";
import headline from "../src/judge/headline.json";

function fakeOidc(): OidcClient {
  return { config: {}, signedIn: () => false, signIn: vi.fn(async () => undefined), handleRedirect: async () => false, accessToken: async () => null, signOut: () => undefined, subscribe: () => () => undefined } as unknown as OidcClient;
}
const DEMO = { status: "ok", sim_time: "2026-01-05T08:00:00Z", demo: { simulated_clock: true, deployment: true } };

function oidcApi(health: () => Promise<unknown>) {
  return { ...createMockAdapter(null), resetDemo: undefined, oidc: fakeOidc(), health } as unknown as ApiClient;
}
const renderApp = (api: ApiClient, route: string) =>
  render(<MemoryRouter initialEntries={[route]}><AppProvider api={api}><App /></AppProvider></MemoryRouter>);

describe("sign-in page while /health is loading", () => {
  it("shows a neutral loading state, not the developer's make oidc-up page, then the role cards", async () => {
    let resolve!: (h: unknown) => void;
    const api = oidcApi(() => new Promise((r) => { resolve = r; }));
    render(<MemoryRouter><AppProvider api={api}><SignInPage /></AppProvider></MemoryRouter>);
    expect(screen.getByTestId("signin-loading")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/Loading/);
    expect(document.body.textContent).not.toMatch(/make oidc-up/);
    expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();
    resolve(DEMO);
    expect(await screen.findByTestId("role-cards")).toBeTruthy();
    expect(screen.queryByTestId("signin-loading")).toBeNull();
  });

  it("falls back to the developer page when /health fails (not the demo)", async () => {
    const api = oidcApi(() => Promise.reject(new Error("down")));
    render(<MemoryRouter><AppProvider api={api}><SignInPage /></AppProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
  });
});

describe("signed out on the hosted demo", () => {
  it.each(["/judge", "/coordinator", "/start"])("%s shows the role-card sign-in page, never the generic one", async (route) => {
    renderApp(oidcApi(async () => DEMO), route);
    expect(await screen.findByTestId("role-cards")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/make oidc-up/);
    expect(screen.queryByTestId("judge-page")).toBeNull();
  });

  it("has a clear secondary link to the public preview under the role cards", async () => {
    renderApp(oidcApi(async () => DEMO), "/judge");
    const link = await screen.findByTestId("preview-link");
    expect(link.getAttribute("href")).toBe("/preview");
    expect(link.textContent).toBe("Preview without signing in");
    const cards = screen.getByTestId("role-cards");
    expect(cards.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(link);
    expect(await screen.findByTestId("preview-page")).toBeTruthy();
  });
});

describe("/preview", () => {
  it("renders signed out, with no request to the API other than the public /health, and none carrying a credential", async () => {
    const seen: Array<{ url: string; auth: string | null }> = [];
    const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      seen.push({ url: String(url), auth: (init?.headers as Record<string, string> | undefined)?.Authorization ?? null });
      return new Response(JSON.stringify(DEMO), { status: 200 });
    });
    const api = createHttpAdapter("http://api.test", fn as unknown as typeof fetch, { oidc: fakeOidc() });
    renderApp(api, "/preview");
    expect(await screen.findByTestId("preview-page")).toBeTruthy();
    await waitFor(() => expect(seen.length).toBeGreaterThan(0)); // the provider's own public /health read
    await new Promise((r) => setTimeout(r, 50));
    expect(seen.every((s) => s.url === "http://api.test/health" && s.auth === null)).toBe(true);
  });

  it("is labelled, has the five steps with alt text and a caption, and the headline numbers with their caveats", async () => {
    renderApp(oidcApi(async () => DEMO), "/preview");
    expect(await screen.findByRole("heading", { level: 1, name: "Preview: screenshots of synthetic data" })).toBeTruthy();
    const steps = screen.getByTestId("preview-steps").querySelectorAll("li");
    expect(steps).toHaveLength(5);
    expect(PREVIEW_STEPS.map((s) => s.id)).toEqual(["record", "prioritise", "refer", "remind", "outcome"]);
    steps.forEach((li) => {
      const img = li.querySelector("img")!;
      expect(img.getAttribute("alt")!.length).toBeGreaterThan(40);
      expect(li.querySelector("figcaption")!.textContent!.length).toBeGreaterThan(20);
    });
    const text = screen.getByTestId("preview-numbers").textContent!;
    expect(text).toContain(`${headline.completion_180d.baseline.value} to ${headline.completion_180d.orchestrated.value}`);
    expect(text).toContain(`${headline.closed_share.baseline.value} to ${headline.closed_share.orchestrated.value}`);
    expect(text).toContain(`${headline.silent_loss.baseline.value} to ${headline.silent_loss.orchestrated_programme.value}`);
    expect(text).toMatch(/modelled/);
    expect(text).toMatch(/40% target is not met/);
    expect(text).not.toMatch(/40% target (is|was) met/);
    expect(screen.getByTestId("preview-back").getAttribute("href")).toBe("/");
  });

  it("is also reachable when signed in, and its images exist and are small", async () => {
    const api = { ...createMockAdapter(null), oidc: { ...fakeOidc(), signedIn: () => true } } as unknown as ApiClient;
    renderApp(api, "/preview");
    expect(await screen.findByTestId("preview-page")).toBeTruthy();
    let total = 0;
    for (const s of PREVIEW_STEPS) {
      const f = `public${s.src}`;
      expect(existsSync(f), f).toBe(true);
      total += statSync(f).size;
    }
    expect(readdirSync("public/preview").length).toBe(PREVIEW_STEPS.length);
    expect(total).toBeLessThan(1_500_000);
  });
});
