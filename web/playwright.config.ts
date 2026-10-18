import { defineConfig } from "@playwright/test";
import { localChromium } from "./pw-chromium";

// E2E_PORT moves the preview server (default 4173), e.g. to avoid reusing a stale preview of another build.
const PORT = Number(process.env.E2E_PORT ?? 4173);

// e2e runs headless against `vite preview` of a mock-adapter build (no backend needed).
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    headless: true,
    screenshot: "only-on-failure",
    launchOptions: { executablePath: localChromium() },
  },
  webServer: {
    command: `VITE_API_ADAPTER=mock npm run build && npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
