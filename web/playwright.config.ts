import { defineConfig } from "@playwright/test";
import { localChromium } from "./pw-chromium";

// e2e runs headless against `vite preview` of a mock-adapter build (no backend needed). The mock build runs with the A21
// automation actor ON (the shipped demo); a spec that needs the manual flow opens its first page with `?automation=off`
// (remembered for the tab) or builds with VITE_MOCK_AUTOMATION=off. Set E2E_PORT when 4173 is taken by another preview:
// a server already listening on the port is reused as is, so it must serve THIS checkout's build.
const PORT = Number(process.env.E2E_PORT ?? 4173);

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
    command: `${process.env.E2E_PREBUILT ? "" : "VITE_API_ADAPTER=mock npm run build && "}npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    // Locally a preview already on the port is reused; in CI (CI is set) a stale server is an error, never reused.
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
