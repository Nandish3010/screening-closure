/**
 * C13: judge mode. The "Judge tour" walks the demo story on the current data (mock adapter, offline), only navigating; the
 * /judge page is a static one-page summary. Both must be axe-clean (WCAG 2.0/2.1 A and AA) in light and dark.
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

test("the tour walks all seven steps on the mock data, only navigating, and Back and Escape work", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (r) => { if (r.method() !== "GET" && r.url().startsWith("http")) posts.push(`${r.method()} ${r.url()}`); });
  await page.goto("/coordinator");
  await page.getByTestId("worklist-row").first().waitFor();
  await page.getByTestId("judge-tour-button").click();
  const tour = page.getByTestId("judge-tour");
  await expect(tour).toBeVisible();
  await expect(page.getByTestId("tour-progress")).toHaveText("Step 1 of 7");
  await expect(tour).toContainText("a positive result can go quiet");
  await expect(page.getByTestId("worklist")).toHaveClass(/tour-highlight/);

  await page.getByTestId("tour-next").click();
  await expect(page.getByTestId("tour-progress")).toHaveText("Step 2 of 7");
  await expect(page).toHaveURL(/\/coordinator\/c0000001-/); // the most overdue case
  await expect(page.getByTestId("timeline")).toHaveClass(/tour-highlight/);

  await page.getByTestId("tour-next").click();
  await expect(tour).toContainText("refuses bad evidence");
  await expect(page.locator("#signoff-panel")).toHaveClass(/tour-highlight/);

  await page.getByTestId("tour-next").click();
  await expect(page).toHaveURL(/\/agency$/);
  await page.getByTestId("tour-next").click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByTestId("effort-card")).toHaveClass(/tour-highlight/);
  await page.getByTestId("tour-next").click();
  await expect(tour).toContainText("where they come from");
  await expect(tour.getByRole("link", { name: /Evaluation results/ })).toHaveAttribute("href", /docs\/results\/results\.md$/);

  await page.getByTestId("tour-back").click();
  await expect(page.getByTestId("tour-progress")).toHaveText("Step 5 of 7");
  await page.getByTestId("tour-next").click();
  await page.getByTestId("tour-next").click();
  await expect(page).toHaveURL(/\/judge$/);
  await expect(page.getByTestId("tour-next")).toHaveCount(0);
  await page.getByTestId("tour-finish").click();
  await expect(tour).toHaveCount(0);

  await page.getByTestId("judge-start-tour").click();
  await expect(tour).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(tour).toHaveCount(0);
  expect(posts, "the tour must not write anything").toEqual([]);
});

test("the tour starts as the coordinator even when another role was selected", async ({ page }) => {
  await page.goto("/coordinator");
  await page.getByLabel("Switch role").selectOption("owner");
  await page.getByTestId("judge-tour-button").click();
  await page.getByTestId("tour-next").click();
  await page.getByTestId("tour-next").click();
  await page.getByTestId("tour-next").click();
  await expect(page.getByLabel("Switch role")).toHaveValue("coordinator");
  await expect(page.getByTestId("agency-page")).toBeVisible();
});

test("the judge page: positioning line, headline with caveats, repo documents, make check", async ({ page }) => {
  await page.goto("/judge");
  await expect(page.getByTestId("judge-positioning")).toContainText("named owner, a clock, an audit trail and a clinician-signed closure");
  await expect(page.getByTestId("judge-headline")).toContainText("18.3%");
  await expect(page.getByTestId("judge-caveats")).toContainText("synthetic");
  await expect(page.getByTestId("judge-caveats")).toContainText("Minutes saved are assumed");
  await expect(page.getByTestId("judge-make-check")).toHaveText("make check");
  expect(await page.getByTestId("judge-docs").getByRole("link").count()).toBeGreaterThanOrEqual(8);
});

for (const scheme of ["light", "dark"] as const) {
  test(`axe: judge page and an open tour, ${scheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/judge");
    await audit(page, `judge page (${scheme})`);
    await page.getByTestId("judge-start-tour").click();
    await page.getByTestId("judge-tour").waitFor();
    await audit(page, `judge page with the tour open (${scheme})`);
    await page.getByTestId("tour-next").click();
    await page.getByTestId("tour-next").click();
    await page.getByTestId("timeline").waitFor();
    await audit(page, `case page with the tour open (${scheme})`);
  });
}
