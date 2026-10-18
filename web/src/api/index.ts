import type { ApiClient } from "./contract";
import { createHttpAdapter } from "./httpAdapter";
import { createMockAdapter } from "./mockAdapter";

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

/** Chosen at build time by VITE_API_ADAPTER=mock|http (default mock). */
export function createClientFromEnv(): ApiClient {
  const env = import.meta.env;
  if (env.VITE_API_ADAPTER === "http") return createHttpAdapter(env.VITE_API_URL || "http://localhost:8000");
  return createMockAdapter(undefined, { automation: mockAutomationFromEnv() });
}
