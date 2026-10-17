import { defineConfig } from "@playwright/test";
import { localChromium } from "./pw-chromium";

// e2e runs headless against `vite preview` of a mock-adapter build (no backend needed).
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:4173",
    headless: true,
    screenshot: "only-on-failure",
    launchOptions: { executablePath: localChromium() },
  },
  webServer: {
    command: "VITE_API_ADAPTER=mock npm run build && npm run preview",
    url: "http://localhost:4173",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
