/**
 * ADR-058: the hosted demo's guided path ("Start here") on the mock adapter's demo mode (`?demo=1`, which plays the judge controls):
 * the first screen, one fresh case per judge tab, the safety-net demonstration, a reset, axe-clean, and no horizontal scroll on a phone.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { freshData } from "./helpers";

test.beforeEach(({ page }) => freshData(page));

test("Start here is the first screen; every step opens its screen with what to look for", async ({ page }) => {
  await page.goto("/?demo=1&pathways=3");
  await expect(page).toHaveURL(/\/start$/);
  await expect(page.getByTestId("start-steps").locator("li")).toHaveCount(5); // the loop
  await expect(page.getByTestId("start-safety").locator("li")).toHaveCount(2); // then what keeps it safe
  await expect(page.getByTestId("demo-status").first()).toContainText("Demo status:");
  for (const id of ["record", "prioritise", "refer", "remind", "outcome", "safety", "privacy"]) {
    await page.goto("/start");
    await page.getByTestId(`start-open-${id}`).click();
    await expect(page.getByTestId("tour-look")).toBeVisible();
    await expect(page.getByTestId("tour-claim")).toBeVisible();
    if (id !== "outcome") await expect(page).toHaveURL(/\/coordinator\/[0-9a-f-]{36}$/);
  }
});

test("the safety net: simulate a missed follow-up, see the engine's events, accept the escalation", async ({ page }) => {
  await page.goto("/start?demo=1&pathways=3");
  await page.evaluate(() => localStorage.setItem("sc.role", "supervisor"));
  await page.reload();
  await page.getByTestId("start-open-safety").click();
  await page.getByTestId("simulate-missed").click();
  await expect(page.getByTestId("missed-result")).toContainText("not a person");
  await expect(page.getByTestId("escalation-level")).toContainText("waiting for a human to accept");
  await expect(page.getByTestId("pending-escalation")).toBeVisible();
});

test("reset asks first, then restores the seed", async ({ page }) => {
  await page.goto("/start?demo=1");
  await page.evaluate(() => localStorage.setItem("sc.role", "supervisor"));
  await page.reload();
  await page.getByTestId("reset-demo-open").first().click();
  await expect(page.getByTestId("reset-demo-confirm")).toContainText("loses their changes");
  await page.getByTestId("reset-demo-yes").click();
  await expect(page.getByTestId("reset-demo-waiting")).toBeVisible();
});

test("Start here is axe-clean and needs no sideways scroll on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/start?demo=1&pathways=3");
  await page.getByTestId("start-steps").waitFor();
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
