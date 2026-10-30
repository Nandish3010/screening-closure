import type { ApiClient } from "./contract";
import { createHttpAdapter } from "./httpAdapter";
import { createMockAdapter } from "./mockAdapter";
import { createOidcClient } from "./oidc";

export * from "./contract";
export { createMockAdapter, createHttpAdapter };

const AUTOMATION_KEY = "sc.mock.automation";

/**
 * Whether the mock plays the automation actor (A21). The shipped mock build runs with automation ON so the demo shows it.
 * Order of precedence: `?automation=on|off` in the URL (remembered for the browser tab, so a spec or a demo can switch
 * it once and navigate freely), then the build-time `VITE_MOCK_AUTOMATION=on|off`, then ON.
 */
export function mockAutomationFromEnv(search: string = typeof location === "undefined" ? "" : location.search): boolean {
  const asBool = (v: string | null | undefined): boolean | null => {
    const t = (v ?? "").trim().toLowerCase();
    if (["on", "true", "1", "yes"].includes(t)) return true;
    if (["off", "false", "0", "no"].includes(t)) return false;
    return null;
  };
  const fromUrl = asBool(new URLSearchParams(search).get("automation"));
  try {
    if (fromUrl !== null) {
      sessionStorage.setItem(AUTOMATION_KEY, fromUrl ? "on" : "off");
      return fromUrl;
    }
    const remembered = asBool(sessionStorage.getItem(AUTOMATION_KEY));
    if (remembered !== null) return remembered;
  } catch {
    /* no sessionStorage: fall through to the build-time default */
  }
  return asBool(import.meta.env.VITE_MOCK_AUTOMATION) ?? true;
}

const PATHWAYS_KEY = "sc.mock.pathways";

/**
 * Whether the mock also seeds the second pathway's cases (cervical screening). OFF by default: `?pathways=2` turns it on (remembered
 * for the browser tab), `?pathways=1` off; the build-time default is `VITE_MOCK_SECOND_PATHWAY=on|off`, else OFF.
 */
export function mockSecondPathwayFromEnv(search: string = typeof location === "undefined" ? "" : location.search): boolean {
  const asBool = (v: string | null | undefined): boolean | null => {
    const t = (v ?? "").trim().toLowerCase();
    if (["2", "on", "true", "yes"].includes(t)) return true;
    if (["1", "off", "false", "no"].includes(t)) return false;
    return null;
  };
  const fromUrl = asBool(new URLSearchParams(search).get("pathways"));
  try {
    if (fromUrl !== null) {
      sessionStorage.setItem(PATHWAYS_KEY, fromUrl ? "on" : "off");
      return fromUrl;
    }
    const remembered = asBool(sessionStorage.getItem(PATHWAYS_KEY));
    if (remembered !== null) return remembered;
  } catch {
    /* no sessionStorage: fall through to the build-time default */
  }
  return asBool(import.meta.env.VITE_MOCK_SECOND_PATHWAY) ?? false;
}

/** Chosen at build time by VITE_API_ADAPTER=mock|http (default mock). */
export function createClientFromEnv(): ApiClient {
  const env = import.meta.env;
  if (env.VITE_API_ADAPTER === "http") {
    const url = env.VITE_API_URL || "http://localhost:8000";
    if (env.VITE_AUTH_MODE !== "oidc") return createHttpAdapter(url);
    const oidc = createOidcClient({
      issuer: env.VITE_OIDC_ISSUER || "http://localhost:8180/realms/screening-closure",
      clientId: env.VITE_OIDC_CLIENT_ID || "screening-closure-web",
      redirectUri: `${location.origin}/`,
    });
    return createHttpAdapter(url, (...a) => fetch(...a), { oidc });
  }
  return createMockAdapter(undefined, { automation: mockAutomationFromEnv(), secondPathway: mockSecondPathwayFromEnv() });
}
