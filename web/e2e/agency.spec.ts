/**
 * A22: the agency page against the mock build. Agents prepare, people decide: the page shows the org chart, what needs a
 * person (with a link to the case, no action button) and the run timeline; the supervisor can start a run.
 */
import { expect, test } from "@playwright/test";
import { freshData } from "./helpers";

const CLOSURE_CASE = "c0000010-0000-4000-8000-000000000010";

test.beforeEach(({ page }) => freshData(page));

test("the agency page: principle, five departments, the inbox, and the way to the case with its chip", async ({ page }) => {
  await page.goto("/coordinator");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Agency" }).click();
  await expect(page).toHaveURL(/\/agency$/);
  await expect(page.getByRole("heading", { level: 1, name: "Agency" })).toBeVisible();
  await expect(page.getByTestId("agency-principle")).toContainText("Agents prepare work; people decide");

  // The org chart: one labelled region per department, with live counts.
  for (const name of ["Outreach", "Referral & Scheduling", "Evidence & Closure", "Patient Support", "Quality & Operations"]) {
    await expect(page.getByRole("region", { name: `${name} department` })).toBeVisible();
  }
  const closure = page.getByTestId("agent-prepare_closure_pack");
  await expect(closure.getByTestId("count-needs_human")).toContainText("1");
  await expect(closure).toContainText("Replaces:");

  // The inbox links to the case and offers no action.
  const item = page.getByTestId("inbox-item").filter({ hasText: "Closure pack ready for sign-off" });
  await expect(item).toBeVisible();
  await expect(page.getByTestId("agency-inbox-panel").getByRole("button")).toHaveCount(0);

  // The run timeline.
  await expect(page.getByTestId("run-select")).toHaveValue("run-0001");
  await expect(page.getByTestId("run-group-outreach").getByTestId("dispatch-rationale")).toBeVisible();

  // Through to the case: the chip says what the agency has there, and the case page's own controls are the way to act.
  await item.getByRole("link", { name: /open case/i }).click();
  await expect(page).toHaveURL(new RegExp(CLOSURE_CASE));
  const chip = page.getByTestId("agency-chip");
  await expect(chip).toHaveText("agency: 1 task, 1 needing you");
  await chip.click();
  await expect(page.getByTestId("agency-chip-list")).toContainText("Closure pack ready for sign-off");
});

test("the supervisor runs the agency: a new run once, then 'already ran at this time'", async ({ page }) => {
  await page.goto("/agency");
  await expect(page.getByTestId("run-agency")).toHaveCount(0); // coordinator: no run button
  await page.getByLabel("Switch role").selectOption("supervisor");
  await expect(page.getByTestId("run-agency")).toBeVisible();
  await page.getByTestId("run-agency").click();
  await expect(page.getByTestId("run-result")).toContainText("run-0002 started");
  await expect(page.getByTestId("run-select")).toHaveValue("run-0002");
  await expect(page.getByTestId("run-group-quality_ops")).toBeVisible();
  await page.getByTestId("run-agency").click();
  await expect(page.getByTestId("run-result")).toContainText("already ran at this time");
});

test("a patient has no agency: no link, and the route says so", async ({ page }) => {
  await page.goto("/agency");
  await page.getByLabel("Switch role").selectOption("patient");
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Agency" })).toHaveCount(0);
  await page.goto("/agency");
  await expect(page.getByTestId("agency-denied")).toContainText("Not available for this role");
});
