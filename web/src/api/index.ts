import type { ApiClient } from "./contract";
import { createHttpAdapter } from "./httpAdapter";
import { createMockAdapter } from "./mockAdapter";

export * from "./contract";
export { createMockAdapter, createHttpAdapter };

/** Chosen at build time by VITE_API_ADAPTER=mock|http (default mock). */
export function createClientFromEnv(): ApiClient {
  const env = import.meta.env;
  if (env.VITE_API_ADAPTER === "http") return createHttpAdapter(env.VITE_API_URL || "http://localhost:8000");
  return createMockAdapter();
}
