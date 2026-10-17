import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * Browser resolution without any download: PW_CHROMIUM_PATH wins; otherwise, if Playwright's own
 * pinned Chromium is missing, fall back to the newest headless shell already in the local cache.
 * (`npx playwright install chromium` also works if you prefer the pinned build.)
 */
export function localChromium(): string | undefined {
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
