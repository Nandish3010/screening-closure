import { defineConfig } from "@playwright/test";
import { localChromium } from "./pw-chromium";

/**
 * The live e2e (`make test-e2e-live`): the same browser, a `vite preview` of an http-adapter build
 * (VITE_API_ADAPTER=http, VITE_API_URL=http://localhost:8000) and the REAL stack behind it (API, Postgres,
 * HAPI, scheduler, watchdog from docker compose, with the seeded cohort). Own port and own build directory
 * so it never reuses the mock build that `make web-e2e` serves on 4173.
 */
const PORT = Number(process.env.LIVE_WEB_PORT ?? 4175);
export default defineConfig({
  testDir: "./e2e-live",
  timeout: 6 * 60_000,
  expect: { timeout: 15_000 },
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
    command: `VITE_API_ADAPTER=http VITE_API_URL=${process.env.LIVE_API_URL ?? "http://localhost:8000"} npx vite build --outDir dist-live && npx vite preview --outDir dist-live --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
