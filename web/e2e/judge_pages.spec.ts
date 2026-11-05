/**
 * The three judge pages (AI and safety, Integrations, Pilot value): reachable from the header and from /judge, no horizontal
 * scroll at phone width, and axe-clean (WCAG 2.0/2.1 A and AA) in light and dark. Mock adapter, offline; the pages only read.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { freshData } from "./helpers";

test.beforeEach(({ page }) => freshData(page));

async function audit(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`), `axe on ${label}`).toEqual([]);
}

const PAGES = [
  { path: "/safety", testid: "safety-page", link: "AI and safety" },
  { path: "/integrations", testid: "integrations-page", link: "Integrations" },
  { path: "/pilot-value", testid: "pilot-value-page", link: "Pilot value" },
] as const;

test("the header and the judge page link to all three pages", async ({ page }) => {
  await page.goto("/judge");
  for (const p of PAGES) {
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: p.link }).click();
    await expect(page.getByTestId(p.testid)).toBeVisible();
    await page.goto("/judge");
  }
  await expect(page.getByTestId("judge-more-pages").getByRole("link")).toHaveCount(3);
});

test("the pilot calculator recomputes in the browser and sends nothing", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (r) => { if (r.method() !== "GET" && r.url().startsWith("http")) posts.push(`${r.method()} ${r.url()}`); });
  await page.goto("/pilot-value");
  await expect(page.getByTestId("pilot-shadow-total")).toContainText("AED 105,828");
  await page.getByLabel("Planned pilot cases").fill("60");
  await expect(page.getByTestId("pilot-shadow-total")).not.toContainText("105,828");
  await expect(page.getByTestId("pilot-target")).toContainText("not met");
  expect(posts).toEqual([]);
});

for (const scheme of ["light", "dark"] as const) {
  for (const p of PAGES) {
    test(`axe: ${p.path}, ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto(p.path);
      await page.getByTestId(p.testid).waitFor();
      await audit(page, `${p.path} (${scheme})`);
    });
  }
}

test("no horizontal scrolling at phone width", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  for (const p of PAGES) {
    await page.goto(p.path);
    await page.getByTestId(p.testid).waitFor();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${p.path} overflows the viewport`).toBeLessThanOrEqual(0);
  }
});
