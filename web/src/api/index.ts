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
 * How many pathways the mock seeds: 1 = bowel only (default), 2 = also cervical screening, 3 = also long-term conditions (whose
 * cases carry a result priority). `?pathways=1|2|3` in the URL is remembered for the browser tab; the build-time default is
 * `VITE_MOCK_SECOND_PATHWAY` (on or 2 = two, 3 = three), else 1. `on`, `true` and `yes` still mean 2 and `off`, `false` and `no` mean 1.
 */
export function mockPathwayCountFromEnv(search: string = typeof location === "undefined" ? "" : location.search): 1 | 2 | 3 {
  const asCount = (v: string | null | undefined): 1 | 2 | 3 | null => {
    const t = (v ?? "").trim().toLowerCase();
    if (t === "3") return 3;
    if (["2", "on", "true", "yes"].includes(t)) return 2;
    if (["1", "off", "false", "no"].includes(t)) return 1;
    return null;
  };
  const fromUrl = asCount(new URLSearchParams(search).get("pathways"));
  try {
    if (fromUrl !== null) {
      sessionStorage.setItem(PATHWAYS_KEY, String(fromUrl));
      return fromUrl;
    }
    const remembered = asCount(sessionStorage.getItem(PATHWAYS_KEY));
    if (remembered !== null) return remembered;
  } catch {
    /* no sessionStorage: fall through to the build-time default */
  }
  return asCount(import.meta.env.VITE_MOCK_SECOND_PATHWAY) ?? 1;
}

/** Whether the mock also seeds the second pathway's cases (cervical screening): `?pathways=2` or `3`. */
export const mockSecondPathwayFromEnv = (search?: string): boolean => mockPathwayCountFromEnv(search) >= 2;
/** Whether the mock also seeds the third pathway's cases (long-term conditions, with priorities): `?pathways=3`. */
export const mockThirdPathwayFromEnv = (search?: string): boolean => mockPathwayCountFromEnv(search) >= 3;

const DEMO_KEY = "sc.mock.demo";

/** Whether the mock plays the hosted demo's judge controls (ADR-058): `?demo=1` (or `=0` to leave), remembered for the browser tab. */
export function mockDemoFromEnv(search: string = typeof location === "undefined" ? "" : location.search): boolean {
  const v = new URLSearchParams(search).get("demo");
  try {
    if (v !== null) {
      const on = ["1", "on", "true", "yes"].includes(v.toLowerCase());
      sessionStorage.setItem(DEMO_KEY, on ? "on" : "off");
      return on;
    }
    return sessionStorage.getItem(DEMO_KEY) === "on";
  } catch {
    return false;
  }
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
  return createMockAdapter(undefined, { automation: mockAutomationFromEnv(), secondPathway: mockSecondPathwayFromEnv(), thirdPathway: mockThirdPathwayFromEnv(), demo: mockDemoFromEnv() });
}
