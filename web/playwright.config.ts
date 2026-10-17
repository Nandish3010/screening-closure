import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

/**
 * Browser resolution without any download: PW_CHROMIUM_PATH wins; otherwise, if Playwright's own
 * pinned Chromium is missing, fall back to the newest headless shell already in the local cache.
 * (`npx playwright install chromium` also works if you prefer the pinned build.)
 */
function localChromium(): string | undefined {
  if (process.env.PW_CHROMIUM_PATH) return process.env.PW_CHROMIUM_PATH;
  for (const root of [join(homedir(), "Library/Caches/ms-playwright"), join(homedir(), ".cache/ms-playwright")]) {
    if (!existsSync(root)) continue;
    const dirs = readdirSync(root).filter((d) => d.startsWith("chromium_headless_shell-")).sort().reverse();
    for (const d of dirs) {
      for (const sub of readdirSync(join(root, d))) {
        const exe = join(root, d, sub, "chrome-headless-shell");
        if (existsSync(exe)) return exe;
      }
    }
  }
  return undefined;
}

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
