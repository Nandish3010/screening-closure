/** Design port: the prototype strip on every route, reflow at 320px, 44px targets in the header, focus ring contrast. */
import { expect, test } from "@playwright/test";
import { freshData } from "./helpers";

const CASE = "c0000001-0000-4000-8000-000000000001";
const PATIENT = "c0000007-0000-4000-8000-000000000007";
const ROUTES = ["/coordinator/today", "/coordinator", `/coordinator/${CASE}`, "/dashboard", `/patient/${PATIENT}`, "/judge", "/safety"];

test.beforeEach(({ page }) => freshData(page));

for (const route of ROUTES) {
  test(`strip and 320px reflow: ${route}`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto(route);
    const strip = page.getByTestId("proto-strip");
    await expect(strip).toContainText("Prototype for FHDIC Challenge 05. Not an official government service.");
    const size = await strip.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(size).toBeGreaterThanOrEqual(13);
    expect(await strip.evaluate((el) => getComputedStyle(el).position)).toBe("sticky");
    await page.waitForTimeout(400);
    const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(wide).toBeLessThanOrEqual(0);
  });
}

test("the role switcher is visible in the header and tagged demo only", async ({ page }) => {
  await page.goto("/coordinator/today");
  await expect(page.getByLabel("Switch role")).toBeVisible();
  await expect(page.locator(".role-switch")).toContainText("demo only");
  const h = await page.getByLabel("Switch role").evaluate((el) => el.getBoundingClientRect().height);
  expect(h).toBeGreaterThanOrEqual(44);
});

test("the keyboard focus ring is dark teal with a white halo on a light page", async ({ page }) => {
  await page.goto("/coordinator/today");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  const ring = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const cs = getComputedStyle(el);
    return { outline: cs.outlineColor, width: cs.outlineWidth, shadow: cs.boxShadow };
  });
  expect(ring.outline).toBe("rgb(10, 70, 85)");
  expect(parseFloat(ring.width)).toBeGreaterThanOrEqual(2);
  expect(ring.shadow).toContain("rgb(255, 255, 255)");
});
