/**
 * T-C22 (C14): the Today screen in a browser. The coordinator lands on it, every queue has a count, the worklist is one click
 * away, an action is reachable and usable from the keyboard, and it fits a phone.
 */
import { expect, test } from "@playwright/test";
import { freshData } from "./helpers";

test.beforeEach(({ page }) => freshData(page));

test("the coordinator lands on Today; the full worklist is one click away", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/coordinator\/today$/);
  await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  await expect(page.getByText("Synthetic data").first()).toBeVisible();
  await expect(page.getByTestId("today-summary")).toBeVisible();
  await expect(page.getByTestId("today-filter-all")).toBeVisible();
  await expect(page.getByTestId("today-filter-overdue")).toBeVisible();
  await page.getByTestId("full-worklist").click();
  await expect(page).toHaveURL(/\/coordinator$/);
  await expect(page.getByTestId("worklist-row").first()).toBeVisible();
});

test("an action works from the keyboard and moves focus to its queue", async ({ page }) => {
  await page.goto("/coordinator/today");
  const row = page.locator('[data-testid="today-item"][data-group="replies"]').filter({ hasText: "number is wrong" });
  const confirm = row.getByTestId("today-confirm_contact");
  await confirm.focus();
  await expect(confirm).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("today-flash")).toContainText("Contact details confirmed");
  await expect(page.locator('[data-testid="today-item"][data-group="replies"]').filter({ hasText: "number is wrong" })).toHaveCount(0);
  await expect(page.locator("#today-h-list")).toBeFocused();
});

test("it fits a phone: no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/coordinator/today");
  await page.getByTestId("today-item").first().waitFor();
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(wide).toBeLessThanOrEqual(0);
});
