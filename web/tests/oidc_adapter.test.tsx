import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { DEMO_USERS, ROLE_NAMES, ROLE_PROVIDERS } from "../src/api/identities";
import type { OidcClient } from "../src/api/oidc";
import { BACKEND_ROLE } from "../src/api/contract";
import { Header } from "../src/components/Header";
import { renderWithApp } from "./helpers";

function fakeOidc(tokens: Array<string | null>): OidcClient & { signIn: ReturnType<typeof vi.fn>; signOut: ReturnType<typeof vi.fn> } {
  const queue = [...tokens];
  let last: string | null = null;
  return {
    config: { issuer: "x", clientId: "web", redirectUri: "x" },
    signedIn: () => true,
    signIn: vi.fn(async () => undefined),
    handleRedirect: async () => false,
    accessToken: vi.fn(async () => (last = queue.length ? queue.shift()! : last)),
    signOut: vi.fn(),
    subscribe: () => () => undefined,
  } as never;
}

function routes(handler: (auth: string | null, url: string) => { status?: number; body: unknown }) {
  const seen: Array<{ url: string; auth: string | null }> = [];
  const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const auth = (init?.headers as Record<string, string> | undefined)?.Authorization ?? null;
    seen.push({ url: String(url), auth });
    const r = handler(auth, String(url));
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200 });
  });
  return { fn: fn as unknown as typeof fetch, seen };
}

describe("http adapter in oidc mode", () => {
  it("sends the provider's access token, never calls dev-login, and login() does nothing", async () => {
    const { fn, seen } = routes(() => ({ body: [] }));
    const api = createHttpAdapter("http://api.test", fn, { oidc: fakeOidc(["AT1"]) });
    await api.login("coordinator", "Maryam Al Suwaidi");
    await api.loginPatientFor?.("c1");
    await api.listCases({});
    expect(seen).toEqual([{ url: "http://api.test/cases?limit=500", auth: "Bearer AT1" }]);
  });

  it("on a 401 refreshes once and retries with the new token", async () => {
    const { fn, seen } = routes((auth) => (auth === "Bearer OLD" ? { status: 401, body: { detail: "invalid token" } } : { body: [] }));
    const oidc = fakeOidc(["OLD", "NEW"]);
    const api = createHttpAdapter("http://api.test", fn, { oidc });
    await api.listCases({});
    expect(seen.map((s) => s.auth)).toEqual(["Bearer OLD", "Bearer NEW"]);
    expect(oidc.signOut).not.toHaveBeenCalled();
  });

  it("signs out when the retry is refused too", async () => {
    const { fn } = routes(() => ({ status: 401, body: { detail: "invalid token" } }));
    const oidc = fakeOidc(["A", "B"]);
    const api = createHttpAdapter("http://api.test", fn, { oidc });
    await expect(api.listCases({})).rejects.toMatchObject({ status: 401 });
    expect(oidc.signOut).toHaveBeenCalled();
  });

  it("/me maps the two primary-care roles apart by provider", async () => {
    const { fn } = routes(() => ({ body: { user_id: "u", role: "primary_care_owner", display_name: "Dr. Huda Rahman", provider: "Marina Digestive Health" } }));
    const api = createHttpAdapter("http://api.test", fn, { oidc: fakeOidc(["AT"]) });
    expect((await api.me()).role).toBe("clinician");
  });
});

describe("header in oidc mode", () => {
  it("lists the demo users under 'Sign in as' and starts a forced sign-in with the username as hint", async () => {
    const user = userEvent.setup();
    const { createMockAdapter } = await import("../src/api/mockAdapter");
    const oidc = fakeOidc(["AT"]);
    renderWithApp(<Header />, { api: { ...createMockAdapter(null), oidc } });
    const select = await screen.findByLabelText("Sign in as");
    expect(screen.queryByLabelText("Switch role")).toBeNull();
    await user.selectOptions(select, "layla.haddad");
    expect(oidc.signIn).toHaveBeenCalledWith({ loginHint: "layla.haddad", forceLogin: true });
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(oidc.signOut).toHaveBeenCalled();
  });
});

describe("the demo users match the committed Keycloak realm", () => {
  const realm = JSON.parse(readFileSync(resolve(__dirname, "../../config/keycloak/screening-closure-realm.json"), "utf8"));
  it("has the six application roles", () => {
    expect(realm.roles.realm.map((r: { name: string }) => r.name).sort()).toEqual(
      ["coordinator", "lab", "patient", "primary_care_owner", "specialist_owner", "supervisor"],
    );
  });
  it.each(DEMO_USERS)("$username has the role (and provider) the UI expects", (u) => {
    const kc = realm.users.find((x: { username: string }) => x.username === u.username);
    expect(kc).toBeTruthy();
    expect(kc.realmRoles).toEqual([BACKEND_ROLE[u.role]]);
    expect(`${kc.firstName} ${kc.lastName}`).toBe(u.name);
    if (ROLE_PROVIDERS[u.role]) expect(kc.attributes.provider).toEqual([ROLE_PROVIDERS[u.role]]);
    expect(ROLE_NAMES[u.role]).toBeTruthy();
  });
  it("commits no plain-text password, only ${PLACEHOLDER}s", () => {
    for (const u of realm.users) for (const c of u.credentials) expect(c.value).toMatch(/^\$\{KC_PW_[A-Z]+\}$/);
  });
});
