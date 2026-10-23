import { webcrypto } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { buildAuthorizeUrl, codeChallenge, createOidcClient, randomString } from "../src/api/oidc";

const CFG = { issuer: "http://idp.test/realms/r", clientId: "web", redirectUri: "http://app.test/" };
const DISCOVERY = {
  authorization_endpoint: "http://idp.test/auth",
  token_endpoint: "http://idp.test/token",
};

function memoryStorage() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    m,
  };
}

function harness(tokenBodies: Array<Record<string, unknown> | number> = []) {
  const storage = memoryStorage();
  const redirect = vi.fn();
  const calls: { url: string; body?: string }[] = [];
  const queue = [...tokenBodies];
  const fetchFn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    calls.push({ url: u, body: init?.body as string | undefined });
    if (u.endsWith("/.well-known/openid-configuration")) return new Response(JSON.stringify(DISCOVERY));
    const next = queue.shift();
    if (typeof next === "number") return new Response("{}", { status: next });
    return new Response(JSON.stringify(next ?? {}));
  }) as unknown as typeof fetch;
  let t = 1_000_000;
  const client = createOidcClient(CFG, {
    fetch: fetchFn, storage, crypto: webcrypto as unknown as Crypto, redirect, now: () => t,
  });
  return { client, storage, redirect, calls, advance: (ms: number) => (t += ms) };
}

describe("PKCE primitives", () => {
  it("S256 challenge matches the RFC 7636 appendix B vector", async () => {
    expect(await codeChallenge(webcrypto as unknown as Crypto, "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });
  it("verifiers are URL-safe, 43 characters for 32 bytes, and not repeated", () => {
    const a = randomString(webcrypto as unknown as Crypto);
    const b = randomString(webcrypto as unknown as Crypto);
    expect(a).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(a).not.toBe(b);
  });
  it("the authorize URL carries the code flow, S256, state and the hint", () => {
    const u = new URL(buildAuthorizeUrl("http://idp.test/auth", CFG, { state: "s1", challenge: "c1", loginHint: "layla.haddad", forceLogin: true }));
    expect(Object.fromEntries(u.searchParams)).toMatchObject({
      response_type: "code", client_id: "web", redirect_uri: "http://app.test/", state: "s1",
      code_challenge: "c1", code_challenge_method: "S256", login_hint: "layla.haddad", prompt: "login", scope: "openid profile",
    });
  });
});

describe("authorization-code flow", () => {
  it("signIn stores state+verifier and redirects with the matching S256 challenge", async () => {
    const { client, storage, redirect } = harness();
    await client.signIn({ loginHint: "maryam.alsuwaidi" });
    const pending = JSON.parse(storage.m.get("sc.oidc.pending")!);
    const url = new URL(redirect.mock.calls[0][0]);
    expect(url.searchParams.get("state")).toBe(pending.state);
    expect(url.searchParams.get("code_challenge")).toBe(await codeChallenge(webcrypto as unknown as Crypto, pending.verifier));
    expect(url.searchParams.get("login_hint")).toBe("maryam.alsuwaidi");
    expect(JSON.stringify([...storage.m])).not.toContain("client_secret");
  });

  it("the callback exchanges the code with the stored verifier and no secret, then signs in", async () => {
    const { client, storage, redirect, calls } = harness([{ access_token: "AT", refresh_token: "RT", expires_in: 300 }]);
    await client.signIn();
    const state = new URL(redirect.mock.calls[0][0]).searchParams.get("state")!;
    const verifier = JSON.parse(storage.m.get("sc.oidc.pending")!).verifier;
    expect(await client.handleRedirect(`?code=abc&state=${state}`)).toBe(true);
    const form = new URLSearchParams(calls.at(-1)!.body);
    expect(Object.fromEntries(form)).toEqual({
      client_id: "web", grant_type: "authorization_code", code: "abc", redirect_uri: "http://app.test/", code_verifier: verifier,
    });
    expect(client.signedIn()).toBe(true);
    expect(await client.accessToken()).toBe("AT");
    expect(storage.m.has("sc.oidc.pending")).toBe(false); // single use
  });

  it("refuses a callback whose state does not match, or that replays", async () => {
    const { client, redirect } = harness([{ access_token: "AT", expires_in: 300 }]);
    await client.signIn();
    await expect(client.handleRedirect("?code=abc&state=forged")).rejects.toThrow(/state/);
    expect(client.signedIn()).toBe(false);
    const state = new URL(redirect.mock.calls[0][0]).searchParams.get("state")!;
    await expect(client.handleRedirect(`?code=abc&state=${state}`)).rejects.toThrow(/state/); // pending was consumed by the failure
  });

  it("surfaces an error answer from the provider", async () => {
    const { client, redirect } = harness();
    await client.signIn();
    const state = new URL(redirect.mock.calls[0][0]).searchParams.get("state")!;
    await expect(client.handleRedirect(`?error=access_denied&error_description=nope&state=${state}`)).rejects.toThrow(/nope/);
  });

  it("a URL that is not a callback is left alone", async () => {
    const { client } = harness();
    expect(await client.handleRedirect("?x=1")).toBe(false);
  });
});

describe("token lifetime", () => {
  async function signedIn(extra: Array<Record<string, unknown> | number>) {
    const h = harness([{ access_token: "AT1", refresh_token: "RT1", expires_in: 300 }, ...extra]);
    await h.client.signIn();
    const state = new URL(h.redirect.mock.calls[0][0]).searchParams.get("state")!;
    await h.client.handleRedirect(`?code=c&state=${state}`);
    return h;
  }

  it("refreshes shortly before expiry with the refresh token", async () => {
    const h = await signedIn([{ access_token: "AT2", expires_in: 300 }]);
    h.advance(280_000); // inside the 30 s margin
    expect(await h.client.accessToken()).toBe("AT2");
    expect(Object.fromEntries(new URLSearchParams(h.calls.at(-1)!.body))).toMatchObject({ grant_type: "refresh_token", refresh_token: "RT1" });
    expect(await h.client.accessToken()).toBe("AT2"); // fresh again: no second request
  });

  it("a failed refresh signs the app out", async () => {
    const h = await signedIn([400]);
    const changed = vi.fn();
    h.client.subscribe(changed);
    h.advance(400_000);
    expect(await h.client.accessToken()).toBeNull();
    expect(h.client.signedIn()).toBe(false);
    expect(changed).toHaveBeenCalled();
  });

  it("expired with no refresh token is signed out", async () => {
    const h = harness([{ access_token: "AT", expires_in: 60 }]);
    await h.client.signIn();
    await h.client.handleRedirect(`?code=c&state=${new URL(h.redirect.mock.calls[0][0]).searchParams.get("state")}`);
    h.advance(120_000);
    expect(await h.client.accessToken()).toBeNull();
  });
});
