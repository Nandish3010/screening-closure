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
  for (const g of ["urgent", "overdue", "escalations", "replies", "flagged", "signatures", "agency"]) {
    await expect(page.getByTestId(`today-count-${g}`)).toBeVisible();
  }
  await page.getByTestId("full-worklist").click();
  await expect(page).toHaveURL(/\/coordinator$/);
  await expect(page.getByTestId("worklist-row").first()).toBeVisible();
});

test("an action works from the keyboard and moves focus to its queue", async ({ page }) => {
  await page.goto("/coordinator/today");
  const row = page.getByTestId("today-group-replies").getByTestId("today-item").filter({ hasText: "number is wrong" });
  const confirm = row.getByTestId("today-confirm_contact");
  await confirm.focus();
  await expect(confirm).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("today-flash")).toContainText("Contact details confirmed");
  await expect(page.getByTestId("today-group-replies").getByTestId("today-item").filter({ hasText: "number is wrong" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Patient replies waiting/ })).toBeFocused();
});

test("it fits a phone: no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/coordinator/today");
  await page.getByTestId("today-item").first().waitFor();
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(wide).toBeLessThanOrEqual(0);
});
