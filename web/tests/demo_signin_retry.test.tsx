/** S1: the one-click sign-in copes with a demo that is still starting up (retries by itself, with a manual button) and with a shared network
 * that has used its allowance (says how long to wait), and the http adapter keeps the API's stable code and Retry-After. */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError, type ApiClient } from "../src/api/contract";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createOidcClient, type OidcClient } from "../src/api/oidc";
import { createMockAdapter } from "../src/api/mockAdapter";
import { AppProvider } from "../src/app/AppContext";
import { SignInPage, SIGNIN_RETRY_MAX_MS, signInIsWarming, signInRetryDelayMs } from "../src/App";

const DEMO = { simulated_clock: true, deployment: true };

async function flush() {
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}
async function tick(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function page(demoSignIn: (r: string) => Promise<void>) {
  const oidc = { config: {}, signedIn: () => false, signIn: vi.fn(), handleRedirect: async () => false, accessToken: async () => null, signOut: () => undefined, adoptTokens: () => undefined, subscribe: () => () => undefined } as unknown as OidcClient;
  const api = { ...createMockAdapter(null), resetDemo: undefined, oidc, demoSignIn, health: async () => ({ status: "ok", sim_time: "2026-01-05T08:00:00Z", demo: DEMO }) } as unknown as ApiClient;
  render(<MemoryRouter><AppProvider api={api}><SignInPage /></AppProvider></MemoryRouter>);
  await flush();
  await flush();
}
const warming = () => new ApiError(503, "The demo is starting up.", 5, "signin_warming_up");

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("while the demo is starting up", () => {
  it("shows a friendly state, retries by itself every few seconds, and signs in when it is ready", async () => {
    let n = 0;
    const demoSignIn = vi.fn(async () => { if (++n < 3) throw warming(); });
    await page(demoSignIn);
    fireEvent.click(screen.getByRole("button", { name: "Log in as Coordinator" }));
    await flush();
    const box = screen.getByTestId("signin-warming");
    expect(box.getAttribute("role")).toBe("status");
    expect(box.textContent).toMatch(/The demo is starting up\. Retrying automatically/);
    expect(document.body.textContent).not.toMatch(/Something went wrong|502|503|Service Unavailable/);
    expect((screen.getByRole("button", { name: /Starting up/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(demoSignIn).toHaveBeenCalledTimes(1);
    await tick(4900);
    expect(demoSignIn).toHaveBeenCalledTimes(1); // the server asked for 5 s
    await tick(100);
    expect(demoSignIn).toHaveBeenCalledTimes(2);
    await tick(5000);
    expect(demoSignIn).toHaveBeenCalledTimes(3);
    expect(screen.queryByTestId("signin-warming")).toBeNull(); // signed in: no more notice
    await tick(60_000);
    expect(demoSignIn).toHaveBeenCalledTimes(3);
  });

  it("treats an edge 502/504 and a network failure like 'starting up', but not a final refusal", () => {
    for (const s of [0, 502, 503, 504]) expect(signInIsWarming(new ApiError(s, "x"))).toBe(true);
    expect(signInIsWarming(new ApiError(503, "x", 60, "signin_locked"))).toBe(false);
    expect(signInIsWarming(new ApiError(503, "x", undefined, "signin_refused"))).toBe(false);
    expect(signInIsWarming(new ApiError(503, "x", undefined, "signin_not_configured"))).toBe(false);
    expect(signInIsWarming(new ApiError(429, "x", 30))).toBe(false);
    expect(signInIsWarming(new Error("x"))).toBe(false);
    expect([0, 1, 2, 3, 4, 8, 30].map((a) => signInRetryDelayMs(a, 5) / 1000)).toEqual([5, 5, 5, 5, 5, 5, 5]);
    expect([0, 1, 2, 4, 8].map((a) => signInRetryDelayMs(a) / 1000)).toEqual([3, 3, 4, 5, 5]);
  });

  it("'Retry now' tries at once", async () => {
    const demoSignIn = vi.fn(async () => { throw warming(); });
    await page(demoSignIn);
    fireEvent.click(screen.getByRole("button", { name: "Log in as Supervisor" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    await flush();
    expect(demoSignIn).toHaveBeenCalledTimes(2);
    expect(demoSignIn).toHaveBeenLastCalledWith("supervisor");
  });

  it("'Stop retrying' stops the automatic tries and gives the buttons back", async () => {
    const demoSignIn = vi.fn(async () => { throw warming(); });
    await page(demoSignIn);
    fireEvent.click(screen.getByRole("button", { name: "Log in as Patient" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Stop retrying" }));
    await tick(30_000);
    expect(demoSignIn).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: "Log in as Patient" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("gives up after about two minutes and offers a manual retry, then retries again from the start", async () => {
    const demoSignIn = vi.fn(async () => { throw warming(); });
    await page(demoSignIn);
    fireEvent.click(screen.getByRole("button", { name: "Log in as Coordinator" }));
    await flush();
    await tick(SIGNIN_RETRY_MAX_MS + 10_000);
    const tries = demoSignIn.mock.calls.length;
    expect(tries).toBeGreaterThan(20);
    expect(tries).toBeLessThan(45);
    const gaveUp = screen.getByTestId("signin-gave-up");
    expect(gaveUp.textContent).toMatch(/longer than usual/);
    await tick(30_000);
    expect(demoSignIn).toHaveBeenCalledTimes(tries); // no more automatic tries
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await flush();
    expect(demoSignIn).toHaveBeenCalledTimes(tries + 1);
    expect(screen.getByTestId("signin-warming")).toBeTruthy();
  });

  it("stops retrying when the page goes away", async () => {
    const demoSignIn = vi.fn(async () => { throw warming(); });
    await page(demoSignIn);
    fireEvent.click(screen.getByRole("button", { name: "Log in as Coordinator" }));
    await flush();
    const { cleanup } = await import("@testing-library/react");
    cleanup();
    await tick(30_000);
    expect(demoSignIn).toHaveBeenCalledTimes(1);
  });
});

describe("when the network has used its allowance (429)", () => {
  it("says how many seconds to wait, counts down, and brings the buttons back", async () => {
    const demoSignIn = vi.fn(async () => { throw new ApiError(429, "Too many sign-ins from this address.", 3, "signin_rate_limited"); });
    await page(demoSignIn);
    fireEvent.click(screen.getByRole("button", { name: "Log in as Coordinator" }));
    await flush();
    const box = screen.getByTestId("signin-limited");
    expect(box.getAttribute("role")).toBe("status");
    expect(box.textContent).toMatch(/Too many sign-ins from this network, please wait 3 seconds/);
    expect((screen.getByRole("button", { name: "Log in as Coordinator" }) as HTMLButtonElement).disabled).toBe(true);
    await tick(1000);
    expect(screen.getByTestId("signin-limited").textContent).toMatch(/wait 2 seconds/);
    await tick(1000);
    expect(screen.getByTestId("signin-limited").textContent).toMatch(/wait 1 second,/);
    await tick(1000);
    expect(screen.queryByTestId("signin-limited")).toBeNull();
    expect((screen.getByRole("button", { name: "Log in as Coordinator" }) as HTMLButtonElement).disabled).toBe(false);
    expect(demoSignIn).toHaveBeenCalledTimes(1); // a 429 is not retried on its own
    expect(screen.getByTestId("password-signin")).toBeTruthy();
  });
});

describe("the http adapter keeps the API's code and Retry-After", () => {
  const oidcFor = (f: typeof fetch) => createOidcClient({ issuer: "https://h/auth/realms/r", clientId: "c", redirectUri: "https://h/" }, { fetch: f, storage: { getItem: () => null, setItem: () => undefined, removeItem: () => undefined } });
  async function fail(res: Response): Promise<ApiError> {
    const f = vi.fn(async () => res) as unknown as typeof fetch;
    return (await createHttpAdapter("https://h/api", f, { oidc: oidcFor(f) }).demoSignIn!("patient").catch((e) => e)) as ApiError;
  }

  it("503 signin_warming_up with Retry-After", async () => {
    const e = await fail(new Response(JSON.stringify({ detail: "The demo is starting up: x", code: "signin_warming_up", retry_after: 5 }), { status: 503, headers: { "Retry-After": "5" } }));
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.retryAfter]).toEqual([503, "signin_warming_up", 5]);
    expect(signInIsWarming(e)).toBe(true);
  });

  it("429 with Retry-After", async () => {
    const e = await fail(new Response(JSON.stringify({ detail: "Too many", code: "signin_rate_limited" }), { status: 429, headers: { "Retry-After": "42" } }));
    expect([e.status, e.code, e.retryAfter]).toEqual([429, "signin_rate_limited", 42]);
  });

  it("an edge 502 with an HTML body is a plain 'starting up', not the raw error", async () => {
    const e = await fail(new Response("<html>502 Bad Gateway</html>", { status: 502, statusText: "Bad Gateway" }));
    expect(e.status).toBe(502);
    expect(e.message).toBe("The demo is starting up.");
    expect(signInIsWarming(e)).toBe(true);
  });
});
