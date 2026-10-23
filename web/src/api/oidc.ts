/**
 * OIDC sign-in for the http adapter (A29, ADR-040): the authorization-code flow with PKCE (RFC 7636, S256) against any
 * OpenID Connect provider (local Keycloak now, UAE PASS later), with no library. The browser is a public client: no
 * secret, the code is bound to a verifier only this tab knows, and `state` ties the callback to the request.
 *
 * Tokens live in sessionStorage (gone when the tab closes; readable by script on the page, which is the usual trade-off
 * of a browser-held token; docs/governance/identity.md lists it). The access token is short; a refresh token, if the
 * provider issues one, renews it shortly before it expires. Everything with a side effect is injected for tests.
 */

export interface OidcConfig {
  /** The provider's issuer URL; endpoints come from `<issuer>/.well-known/openid-configuration`. */
  issuer: string;
  clientId: string;
  /** Where the provider sends the browser back (registered with the provider). */
  redirectUri: string;
  scope?: string;
}

interface Discovery {
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

export interface OidcSession {
  accessToken: string;
  refreshToken?: string;
  /** Epoch milliseconds. */
  expiresAt: number;
}

export interface OidcDeps {
  fetch: typeof fetch;
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem">;
  crypto: Pick<Crypto, "getRandomValues" | "subtle">;
  /** Send the browser to the provider (default: `location.assign`). */
  redirect: (url: string) => void;
  now: () => number;
}

const SESSION_KEY = "sc.oidc.session";
const PENDING_KEY = "sc.oidc.pending";
const REFRESH_MARGIN_MS = 30_000;

export function base64Url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** A random URL-safe string of `byteLength` random bytes (RFC 7636 wants a 43 to 128 character verifier: 32 bytes -> 43). */
export function randomString(c: Pick<Crypto, "getRandomValues">, byteLength = 32): string {
  return base64Url(c.getRandomValues(new Uint8Array(byteLength)));
}

/** `BASE64URL(SHA256(ASCII(verifier)))`: the S256 code challenge. */
export async function codeChallenge(c: Pick<Crypto, "subtle">, verifier: string): Promise<string> {
  const digest = await c.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

export function buildAuthorizeUrl(
  endpoint: string,
  cfg: OidcConfig,
  p: { state: string; challenge: string; loginHint?: string; forceLogin?: boolean },
): string {
  const u = new URL(endpoint);
  u.searchParams.set("response_type", "code");
  u.searchParams.set("client_id", cfg.clientId);
  u.searchParams.set("redirect_uri", cfg.redirectUri);
  u.searchParams.set("scope", cfg.scope ?? "openid profile");
  u.searchParams.set("state", p.state);
  u.searchParams.set("code_challenge", p.challenge);
  u.searchParams.set("code_challenge_method", "S256");
  if (p.loginHint) u.searchParams.set("login_hint", p.loginHint);
  if (p.forceLogin) u.searchParams.set("prompt", "login");
  return u.toString();
}

export class OidcError extends Error {}

export interface OidcClient {
  config: OidcConfig;
  signedIn(): boolean;
  /** Begin the flow: stores state and verifier, then redirects. `loginHint` pre-fills the username; `forceLogin` ignores any provider session. */
  signIn(opts?: { loginHint?: string; forceLogin?: boolean }): Promise<void>;
  /** On the redirect back: exchange `?code=&state=` for tokens. Resolves true if this URL was a callback that succeeded. Rejects on a bad state or an error answer. */
  handleRedirect(search: string): Promise<boolean>;
  /** A usable access token (refreshed first if it is about to expire), or null when signed out. */
  accessToken(opts?: { force?: boolean }): Promise<string | null>;
  /** Forget the tokens (the app is signed out; the provider session is separate). */
  signOut(): void;
  subscribe(listener: () => void): () => void;
}

export function createOidcClient(cfg: OidcConfig, deps: Partial<OidcDeps> = {}): OidcClient {
  const d: OidcDeps = {
    fetch: deps.fetch ?? ((...a) => fetch(...a)),
    storage: deps.storage ?? sessionStorage,
    crypto: deps.crypto ?? crypto,
    redirect: deps.redirect ?? ((url) => location.assign(url)),
    now: deps.now ?? Date.now,
  };
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => l());
  let discovery: Promise<Discovery> | null = null;
  let refreshing: Promise<OidcSession | null> | null = null;

  function load(): OidcSession | null {
    try {
      const raw = d.storage.getItem(SESSION_KEY);
      return raw ? (JSON.parse(raw) as OidcSession) : null;
    } catch {
      return null;
    }
  }
  function save(s: OidcSession | null): void {
    try {
      if (s) d.storage.setItem(SESSION_KEY, JSON.stringify(s));
      else d.storage.removeItem(SESSION_KEY);
    } catch {
      /* storage unavailable: the session lasts only until reload */
    }
    emit();
  }

  function endpoints(): Promise<Discovery> {
    discovery ??= d
      .fetch(`${cfg.issuer.replace(/\/+$/, "")}/.well-known/openid-configuration`, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok) throw new OidcError(`provider discovery failed (${r.status})`);
        return (await r.json()) as Discovery;
      })
      .catch((e) => {
        discovery = null; // retry next time
        throw e;
      });
    return discovery;
  }

  async function tokenRequest(form: Record<string, string>): Promise<OidcSession> {
    const { token_endpoint } = await endpoints();
    const res = await d.fetch(token_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: new URLSearchParams({ client_id: cfg.clientId, ...form }).toString(),
    });
    if (!res.ok) throw new OidcError(`token request failed (${res.status})`);
    const j = (await res.json()) as { access_token?: string; refresh_token?: string; expires_in?: number };
    if (!j.access_token) throw new OidcError("token response without access_token");
    return { accessToken: j.access_token, refreshToken: j.refresh_token, expiresAt: d.now() + (j.expires_in ?? 300) * 1000 };
  }

  return {
    config: cfg,
    signedIn: () => load() !== null,
    subscribe(l) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    async signIn(opts = {}) {
      const { authorization_endpoint } = await endpoints();
      const state = randomString(d.crypto);
      const verifier = randomString(d.crypto);
      d.storage.setItem(PENDING_KEY, JSON.stringify({ state, verifier }));
      save(null);
      d.redirect(buildAuthorizeUrl(authorization_endpoint, cfg, {
        state, challenge: await codeChallenge(d.crypto, verifier), loginHint: opts.loginHint, forceLogin: opts.forceLogin,
      }));
    },
    async handleRedirect(search) {
      const q = new URLSearchParams(search);
      const code = q.get("code");
      const error = q.get("error");
      if (!code && !error) return false;
      const raw = d.storage.getItem(PENDING_KEY);
      d.storage.removeItem(PENDING_KEY); // one use, whatever happens
      const pending = raw ? (JSON.parse(raw) as { state: string; verifier: string }) : null;
      if (!pending || q.get("state") !== pending.state) throw new OidcError("sign-in response does not match the request (state)");
      if (error) throw new OidcError(`sign-in refused: ${q.get("error_description") ?? error}`);
      save(await tokenRequest({ grant_type: "authorization_code", code: code!, redirect_uri: cfg.redirectUri, code_verifier: pending.verifier }));
      return true;
    },
    async accessToken(opts = {}) {
      const s = load();
      if (!s) return null;
      if (!opts.force && s.expiresAt - REFRESH_MARGIN_MS > d.now()) return s.accessToken;
      if (!s.refreshToken) {
        save(null);
        return null;
      }
      refreshing ??= tokenRequest({ grant_type: "refresh_token", refresh_token: s.refreshToken })
        .then((n) => (save({ ...n, refreshToken: n.refreshToken ?? s.refreshToken }), n))
        .catch(() => (save(null), null))
        .finally(() => {
          refreshing = null;
        });
      return (await refreshing)?.accessToken ?? null;
    },
    signOut() {
      save(null);
    },
  };
}
