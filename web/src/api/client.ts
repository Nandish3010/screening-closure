import type { ApiClient } from "./contract";
import { createHttpAdapter } from "./httpAdapter";
import { createOidcClient } from "./oidc";

/**
 * The client the app boots with. Kept apart from `./index` so a hosted build (VITE_API_ADAPTER=http) does not bundle the mock
 * adapter and its fixtures: the mock modules run code when imported, so they are not tree-shaken once anything imports them
 * statically. Here the mock is only reached through a dynamic import behind a build-time constant, so an http build never
 * emits it (about 100 KB of JS less on the first load).
 */
export function createHttpClientFromEnv(): ApiClient {
  const env = import.meta.env;
  const url = env.VITE_API_URL || "http://localhost:8000";
  if (env.VITE_AUTH_MODE !== "oidc") return createHttpAdapter(url);
  const oidc = createOidcClient({
    issuer: env.VITE_OIDC_ISSUER || "http://localhost:8180/realms/screening-closure",
    clientId: env.VITE_OIDC_CLIENT_ID || "screening-closure-web",
    redirectUri: `${location.origin}/`,
  });
  return createHttpAdapter(url, (...a) => fetch(...a), { oidc });
}

/** Chosen at build time by VITE_API_ADAPTER=mock|http (default mock). */
export async function createClientFromEnv(): Promise<ApiClient> {
  if (import.meta.env.VITE_API_ADAPTER === "http") return createHttpClientFromEnv();
  return (await import("./index")).createClientFromEnv();
}
