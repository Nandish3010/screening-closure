/**
 * C11 / T-C15: accessibility (axe-core, WCAG 2.0/2.1 A and AA) on the three surfaces, in light and dark, and the PWA
 * installability checks. Fails on any violation of impact "serious" or "critical"; lesser ones are printed.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { OVERDUE_CASE, PATIENT_CASE, freshData } from "./helpers";

test.beforeEach(({ page }) => freshData(page));

async function audit(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const minor = results.violations.filter((v) => v.impact !== "serious" && v.impact !== "critical");
  if (minor.length) console.log(`axe (${label}) non-blocking: ${minor.map((v) => `${v.id}[${v.impact}]`).join(", ")}`);
  const summary = bad.map((v) => `${v.id} [${v.impact}]: ${v.help}\n   ${v.nodes.slice(0, 3).map((n) => n.target.join(" ") + " :: " + (n.failureSummary ?? "").replace(/\n/g, " ")).join("\n   ")}`);
  expect(summary, `serious or critical axe violations on ${label}`).toEqual([]);
}

test("the audit itself works: axe finds a missing alt text and an unnamed button", async ({ page }) => {
  await page.setContent('<!doctype html><html lang="en"><head><title>t</title></head><body><main><img src="x.png"><button></button></main></body></html>');
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const ids = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
  expect(ids).toEqual(expect.arrayContaining(["image-alt", "button-name"]));
});

for (const scheme of ["light", "dark"] as const) {
  test.describe(`axe, ${scheme} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
    });

    test("coordinator app: worklist", async ({ page }) => {
      await page.goto("/coordinator");
      await page.getByTestId("worklist-row").first().waitFor();
      await audit(page, `worklist (${scheme})`);
    });

    test("coordinator app: case page with the drafts panel, as coordinator", async ({ page }) => {
      await page.goto(`/coordinator/${OVERDUE_CASE}`);
      await page.getByTestId("request-draft").click();
      await page.getByTestId(/^draft-d-mock-/).waitFor();
      await audit(page, `case page, coordinator (${scheme})`);
    });

    test("coordinator app: case page as the owner (approve and reject controls enabled)", async ({ page }) => {
      await page.goto(`/coordinator/${OVERDUE_CASE}`);
      await page.getByLabel("Switch role").selectOption("owner");
      await page.getByTestId("request-draft").click();
      await page.getByTestId(/^draft-d-mock-/).waitFor();
      await audit(page, `case page, owner (${scheme})`);
    });

    test("coordinator app: a bounced hand-off and a frozen case", async ({ page }) => {
      await page.goto("/coordinator/c0000015-0000-4000-8000-000000000015");
      await page.getByTestId("handoff-bounced").waitFor();
      await audit(page, `bounced hand-off (${scheme})`);
      await page.goto("/coordinator/c0000014-0000-4000-8000-000000000014");
      await page.getByTestId("consent-frozen-banner").waitFor();
      await audit(page, `frozen consent (${scheme})`);
      // D13: a case flagged by the reply triage (banner, held messages, the reply events on the timeline).
      await page.goto("/coordinator/c0000021-0000-4000-8000-000000000021");
      await page.getByTestId("triage-review-banner").waitFor();
      await audit(page, `triage-flagged case (${scheme})`);
    });

    test("patient app, English", async ({ page }) => {
      await page.goto(`/patient/${PATIENT_CASE}`);
      await page.getByTestId("patient-owner").waitFor();
      await audit(page, `patient en (${scheme})`);
    });

    test("patient app, Arabic, with consent withdrawn", async ({ page }) => {
      await page.goto(`/patient/${PATIENT_CASE}`);
      await page.getByRole("button", { name: /العربية/ }).click();
      await page.getByTestId("consent-withdraw").click();
      await page.getByTestId("consent-withdrawn").waitFor();
      await audit(page, `patient ar (${scheme})`);
    });

    test("agency page, as coordinator and as supervisor, and the chip on a case", async ({ page }) => {
      await page.goto("/agency");
      await page.getByTestId("run-detail").waitFor();
      await page.getByTestId("run-group-outreach").waitFor();
      await audit(page, `agency, coordinator (${scheme})`);
      await page.getByLabel("Switch role").selectOption("supervisor");
      await page.getByTestId("run-agency").click();
      await page.getByTestId("run-result").waitFor();
      await audit(page, `agency, supervisor after a run (${scheme})`);
      await page.goto(`/coordinator/${OVERDUE_CASE}`);
      await page.getByTestId("agency-chip").click();
      await page.getByTestId("agency-chip-list").waitFor();
      await audit(page, `case page with the agency chip open (${scheme})`);
    });

    test("closure dashboard, with the scheduler banner", async ({ page }) => {
      await page.goto("/dashboard");
      await page.getByTestId("kpi-p50").waitFor();
      await audit(page, `dashboard (${scheme})`);
      await page.getByTestId("toggle-scheduler").click();
      await page.getByTestId("scheduler-banner").waitFor();
      await audit(page, `dashboard with scheduler banner (${scheme})`);
    });
  });
}

test.describe("PWA (T-C15)", () => {
  test("the manifest has what an installable app needs, and every icon it names is served", async ({ page }) => {
    await page.goto(`/patient/${PATIENT_CASE}`);
    // The page links the manifest.
    const href = await page.locator('link[rel="manifest"]').getAttribute("href");
    expect(href).toBe("/manifest.webmanifest");
    const res = await page.request.get(href!);
    expect(res.ok()).toBe(true);
    expect(res.headers()["content-type"]).toMatch(/json|manifest/);
    const m = await res.json();
    expect(m.name).toBeTruthy();
    expect(m.short_name).toBeTruthy();
    expect(m.start_url).toBe("/patient");
    expect(m.scope).toBe("/");
    expect(m.display).toBe("standalone");
    expect(m.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(m.background_color).toMatch(/^#[0-9a-f]{6}$/i);
    const sizes = m.icons.map((i: { sizes: string }) => i.sizes);
    expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    for (const icon of m.icons as { src: string; type: string }[]) {
      const r = await page.request.get(icon.src);
      expect(r.ok(), icon.src).toBe(true);
      expect(r.headers()["content-type"], icon.src).toContain(icon.type.replace("image/svg+xml", "svg"));
    }
    // The start URL is inside the scope and renders without a backend.
    await page.goto(m.start_url);
    await expect(page.getByTestId("patient-root")).toBeVisible();
    // Theme color and viewport are declared for the installed shell.
    await expect(page.locator('meta[name="theme-color"]').first()).toHaveAttribute("content", /#/);
    await expect(page.locator('meta[name="viewport"]')).toHaveAttribute("content", /width=device-width/);
  });

  test("a service worker with a fetch handler is active and controls the page, which is what makes it installable and offline-capable", async ({ page }) => {
    const sw = await page.request.get("/sw.js");
    expect(sw.ok()).toBe(true);
    expect(await sw.text()).toMatch(/addEventListener\(\s*["']fetch["']/);
    await page.goto(`/patient/${PATIENT_CASE}`);
    const state = await page.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready;
      // `ready` resolves once a worker is active, which can be a moment before its state reads "activated".
      for (let i = 0; i < 50 && reg.active?.state !== "activated"; i++) await new Promise((r) => setTimeout(r, 100));
      return { scope: new URL(reg.scope).pathname, active: reg.active?.state };
    });
    expect(state).toEqual({ scope: "/", active: "activated" });
    await page.reload();
    expect(await page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);
  });
});
