// Regenerates web/public/preview/*.webp: screenshots of the signed-in screens, taken from the web app running against its
// MOCK adapter (synthetic data only), for the public "Preview without signing in" page (/preview).
//   cd web && node scripts/preview-shots.mjs      (needs `npm ci`, Playwright's chromium and `cwebp` on PATH)
// It starts vite on port 5221 itself and stops exactly that process by PID when it is done.
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "public", "preview");
const tmp = join(root, "scripts", ".preview-tmp");
const PORT = 5221;
const BASE = `http://localhost:${PORT}`;
const OVERDUE = "c0000001-0000-4000-8000-000000000001";
const PATIENT = "c0000007-0000-4000-8000-000000000007";

mkdirSync(out, { recursive: true });
mkdirSync(tmp, { recursive: true });
const vite = spawn(join(root, "node_modules", ".bin", "vite"), ["--port", String(PORT), "--strictPort"], {
  cwd: root, env: { ...process.env, VITE_API_ADAPTER: "mock" }, stdio: "ignore",
});
const stop = () => { try { process.kill(vite.pid); } catch { /* already gone */ } };
process.on("exit", stop);

async function waitUp() {
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch(BASE)).ok) return; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("vite did not start");
}

// name, route, element to wait for, optional scroll target (to show the relevant part of a long page), pixels above it to keep
const SHOTS = [
  ["record", `/coordinator/${OVERDUE}`, '[data-testid="timeline"]', '[data-testid="timeline"]', 90],
  ["prioritise", "/coordinator?pathways=3", '[data-testid="worklist-row"]', "table", 24],
  ["refer", `/coordinator/${OVERDUE}`, '[data-testid="insurance"]', '[data-testid="insurance"]', 24],
  ["remind", `/patient/${PATIENT}`, '[data-testid="patient-owner"]', '[data-testid="patient-owner"]', 300],
  ["outcome", "/dashboard", '[data-testid="funnel-card"]', '[data-testid="funnel-card"]', 24],
];

try {
  await waitUp();
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "light", reducedMotion: "reduce" });
  const page = await ctx.newPage();
  for (const [name, route, wait, scroll, above] of SHOTS) {
    await page.goto(BASE + route);
    await page.locator(wait).first().waitFor();
    // The sticky strip and header would cover the part of the screen being shown; they are not what the step is about.
    await page.addStyleTag({ content: ".proto-strip, header, .app-header, .site-header { position: static !important; }" });
    if (scroll) await page.locator(scroll).first().evaluate((el, a) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - a), above);
    await page.waitForTimeout(500);
    const png = join(tmp, `${name}.png`);
    await page.screenshot({ path: png });
    execFileSync("cwebp", ["-quiet", "-q", "80", "-m", "6", png, "-o", join(out, `${name}.webp`)]);
    console.log(name, statSync(join(out, `${name}.webp`)).size, "bytes");
  }
  await browser.close();
} finally {
  stop();
  rmSync(tmp, { recursive: true, force: true });
}
