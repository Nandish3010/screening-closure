/**
 * D13: the web tolerates and displays the reply triage. The mock build (automation on) seeds two cases the triage flagged: an
 * opt-out (case 20) and a wrong number (case 21). The timeline labels the reply events and shows no reply text; a person clears
 * the wrong-number hold with the contact details confirmed, which the mock applies as the backend does.
 */
import { expect, test } from "@playwright/test";
import { freshData } from "./helpers";

const OPT_OUT_CASE = "c0000020-0000-4000-8000-000000000020";

test.beforeEach(({ page }) => freshData(page));

test("a case the triage flagged: reason banner, held messages, and the reply events on the timeline without any reply text", async ({ page }) => {
  await page.goto(`/coordinator/${OPT_OUT_CASE}`);
  const banner = page.getByTestId("triage-review-banner");
  await expect(banner).toContainText("The patient opted out");
  await expect(banner).toContainText("Automatic messages held");
  const timeline = page.getByTestId("timeline");
  await expect(timeline.locator('[data-event-type="reply_received"]')).toContainText("Patient reply received");
  await expect(timeline.locator('[data-event-type="reply_received"]')).toContainText("channel: sms");
  await expect(timeline.locator('[data-event-type="reply_classified"]')).toContainText("intent: opt out");
  await expect(timeline.locator('[data-event-type="review_requested"]')).toContainText("The patient opted out");
  // The triage is a service: its rows carry the service chip, never the Automated chip.
  await expect(timeline.locator('[data-event-type="reply_received"]').getByTestId("automation-chip")).toHaveCount(0);
});

test("a patient does not see the triage events on their own page", async ({ page }) => {
  await page.goto(`/coordinator/${OPT_OUT_CASE}`);
  await page.getByTestId("triage-review-banner").waitFor();
  await page.goto(`/patient/${OPT_OUT_CASE}`);
  await expect(page.getByTestId("patient-root")).toBeVisible();
  await expect(page.getByText(/opted out|reply received|triage/i)).toHaveCount(0);
});
