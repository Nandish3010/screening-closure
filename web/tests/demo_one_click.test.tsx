/** ADR-060: on the hosted demo each role card is one click ("Log in as <role>"), no password in the page, tokens stored like the normal sign-in. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { readFileSync } from "node:fs";
import type { ApiClient } from "../src/api/contract";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createOidcClient, type OidcClient } from "../src/api/oidc";
import { createMockAdapter } from "../src/api/mockAdapter";
import { AppProvider } from "../src/app/AppContext";
import { SignInPage } from "../src/App";

const DEMO = { simulated_clock: true, deployment: true };

function memory(): Pick<Storage, "getItem" | "setItem" | "removeItem"> {
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v), removeItem: (k) => void m.delete(k) };
}
const CFG = { issuer: "https://h/auth/realms/r", clientId: "screening-closure-web", redirectUri: "https://h/" };

function page(demoSignIn?: (r: string) => Promise<void>) {
  const signIn = vi.fn(async () => undefined);
  const oidc = { config: {}, signedIn: () => false, signIn, handleRedirect: async () => false, accessToken: async () => null, signOut: () => undefined, adoptTokens: () => undefined, subscribe: () => () => undefined } as unknown as OidcClient;
  const api = { ...createMockAdapter(null), resetDemo: undefined, oidc, demoSignIn, health: async () => ({ status: "ok", sim_time: "2026-01-05T08:00:00Z", demo: DEMO }) } as unknown as ApiClient;
  render(<MemoryRouter><AppProvider api={api}><SignInPage /></AppProvider></MemoryRouter>);
  return { signIn };
}

describe("one-click role buttons", () => {
  it("show 'Log in as ...' for the four roles, call the sign-in with that role, and show no username or password", async () => {
    const demoSignIn = vi.fn(async () => undefined);
    page(demoSignIn);
    await screen.findByTestId("role-cards");
    for (const name of ["Coordinator", "Supervisor", "Physician", "Patient"]) expect(screen.getByRole("button", { name: `Log in as ${name}` })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Username:|maryam|noura|huda|password for each role/i);
    fireEvent.click(screen.getByRole("button", { name: "Log in as Supervisor" }));
    await waitFor(() => expect(demoSignIn).toHaveBeenCalledWith("supervisor"));
    fireEvent.click(screen.getByRole("button", { name: "Log in as Physician" }));
    await waitFor(() => expect(demoSignIn).toHaveBeenCalledWith("clinician"));
  });

  it("keeps a 'Operator sign-in (password)' link to the normal flow, and the preview link", async () => {
    const { signIn } = page(vi.fn(async () => undefined));
    fireEvent.click(await screen.findByTestId("password-signin"));
    expect(signIn).toHaveBeenCalledWith({ forceLogin: true });
    expect(screen.getByTestId("preview-link").getAttribute("href")).toBe("/preview");
  });

  it("says why when the one-click route fails, and the other ways in stay available", async () => {
    page(vi.fn(async () => { throw new Error("Many judges are signing in right now. Please try again in a minute."); }));
    fireEvent.click(await screen.findByRole("button", { name: "Log in as Coordinator" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Many judges/);
    expect(screen.getByTestId("password-signin")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Log in as Coordinator" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("falls back to the username-prefilled password flow when the adapter has no one-click route", async () => {
    const { signIn } = page(undefined);
    fireEvent.click(await screen.findByRole("button", { name: /sign in as supervisor/i }));
    expect(signIn).toHaveBeenCalledWith({ loginHint: "noura.alhammadi", forceLogin: true });
    expect(screen.queryByTestId("password-signin")).toBeNull();
  });
});

describe("the http adapter's demoSignIn", () => {
  const tokens = { access_token: "A1", refresh_token: "R1", expires_in: 300, client_id: "screening-closure-demo", token_type: "Bearer" };

  it("POSTs only the role, stores the tokens through the OIDC client, and refreshes them as the demo client", async () => {
    let now = 1_000_000;
    const calls: Array<{ url: string; body: string }> = [];
    const f = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), body: String(init?.body ?? "") });
      if (String(url).endsWith("/demo/sign-in")) return new Response(JSON.stringify(tokens), { status: 200 });
      if (String(url).endsWith("/.well-known/openid-configuration")) return new Response(JSON.stringify({ authorization_endpoint: "x", token_endpoint: "https://h/auth/token" }), { status: 200 });
      return new Response(JSON.stringify({ access_token: "A2", refresh_token: "R2", expires_in: 300 }), { status: 200 });
    }) as unknown as typeof fetch;
    const storage = memory();
    const oidc = createOidcClient(CFG, { fetch: f, storage, now: () => now });
    const api = createHttpAdapter("https://h/api", f, { oidc });
    expect(oidc.signedIn()).toBe(false);
    await api.demoSignIn!("clinician");
    expect(calls[0]).toEqual({ url: "https://h/api/demo/sign-in", body: JSON.stringify({ role: "clinician" }) });
    expect(oidc.signedIn()).toBe(true);
    expect(await oidc.accessToken()).toBe("A1");
    expect(storage.getItem("sc.oidc.session")).toContain("screening-closure-demo");
    now += 400_000; // expired: the refresh names the client that issued the tokens
    expect(await oidc.accessToken()).toBe("A2");
    expect(calls.at(-1)!.body).toContain("client_id=screening-closure-demo");
    expect(calls.at(-1)!.body).toContain("grant_type=refresh_token");
    oidc.signOut();
    expect(oidc.signedIn()).toBe(false);
  });

  it("rejects with the server's reason and stays signed out; the mock and a non-oidc adapter have no such method", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ detail: "Too many sign-ins from this address." }), { status: 429 })) as unknown as typeof fetch;
    const oidc = createOidcClient(CFG, { fetch: f, storage: memory() });
    await expect(createHttpAdapter("https://h/api", f, { oidc }).demoSignIn!("patient")).rejects.toThrow(/Too many/);
    expect(oidc.signedIn()).toBe(false);
    expect(createHttpAdapter("https://h/api", f).demoSignIn).toBeUndefined();
    expect(createMockAdapter(null).demoSignIn).toBeUndefined();
  });

  it("the bundle holds no password or secret for it", () => {
    const src = ["src/api/httpAdapter.ts", "src/api/oidc.ts", "src/App.tsx", "src/api/identities.ts"].map((f) => readFileSync(f, "utf8")).join("\n");
    expect(src).not.toMatch(/password\s*[:=]\s*["'`]/i);
    expect(src).not.toMatch(/VITE_[A-Z_]*(PASSWORD|SECRET)/);
  });
});

describe("the header's 'Sign in as' on the hosted demo", () => {
  async function header(demoSignIn: (r: string) => Promise<void>) {
    const { renderWithApp } = await import("./helpers");
    const { Header } = await import("../src/components/Header");
    const signIn = vi.fn(async () => undefined);
    const oidc = { config: {}, signedIn: () => true, signIn, handleRedirect: async () => false, accessToken: async () => "AT", signOut: () => undefined, adoptTokens: () => undefined, subscribe: () => () => undefined } as unknown as OidcClient;
    const api = { ...createMockAdapter(null), oidc, demoSignIn, health: async () => ({ status: "ok", sim_time: "2026-01-05T08:00:00Z", demo: DEMO }) } as unknown as ApiClient;
    renderWithApp(<Header />, { api });
    return { signIn, select: await screen.findByLabelText("Sign in as") };
  }

  it("is one click: signs in as the chosen role, then starts fresh", async () => {
    const assign = vi.fn();
    const saved = window.location;
    Object.defineProperty(window, "location", { configurable: true, value: { ...saved, assign } });
    try {
      const demoSignIn = vi.fn(async () => undefined);
      const { signIn, select } = await header(demoSignIn);
      const { default: userEvent } = await import("@testing-library/user-event");
      await waitFor(() => expect(screen.getByRole("option", { name: /Physician/ })).toBeTruthy());
      await userEvent.setup().selectOptions(select, "noura.alhammadi");
      await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
      expect(demoSignIn).toHaveBeenCalledWith("supervisor");
      expect(signIn).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: saved });
    }
  });

  it("falls back to the password sign-in when the one-click route fails", async () => {
    const { signIn, select } = await header(vi.fn(async () => { throw new Error("down"); }));
    const { default: userEvent } = await import("@testing-library/user-event");
    await waitFor(() => expect(screen.getByRole("option", { name: /Physician/ })).toBeTruthy());
    await userEvent.setup().selectOptions(select, "huda.rahman");
    await waitFor(() => expect(signIn).toHaveBeenCalledWith({ loginHint: "huda.rahman", forceLogin: true }));
  });
});
