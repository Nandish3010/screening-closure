/**
 * M6 week 2 (A33, A34, ADR-042) end to end on the mock adapter: an informed refusal that a second physician signs, and a
 * cancelled appointment that is booked again. Case 9 is confirmatory_booked and owned by the specialist (Dr. Karim Nasser).
 */
import { expect, test } from "@playwright/test";
import { freshData } from "./helpers";

const BOOKED_CASE = "c0000009-0000-4000-8000-000000000009";

test.beforeEach(({ page }) => freshData(page));

test("A33: the owner records a refusal, cannot sign it, a coordinator reassigns, another physician closes it as declined", async ({ page }) => {
  await page.goto(`/coordinator/${BOOKED_CASE}`);
  await page.getByLabel("Switch role").selectOption("specialist");
  await expect(page.getByTestId("refusal-panel")).toBeVisible();
  // the record needs a counselling record that is a DocumentReference
  await expect(page.getByTestId("record-refusal")).toBeDisabled();
  await page.getByTestId("counselling-ref").fill("DocumentReference/counselling-77");
  await page.getByTestId("counselled-by").selectOption("specialist");
  await page.getByTestId("refusal-reason").selectOption("fear_of_procedure");
  await page.getByTestId("record-refusal").click();
  await expect(page.getByTestId("flash")).toContainText("Informed refusal recorded");
  await expect(page.getByTestId("declined-signoff-panel")).toContainText("declined after counselling");
  await expect(page.getByTestId("re-offer-due")).toBeVisible();
  // the counsellor cannot sign their own counselling
  await expect(page.getByTestId("sign-declined")).toBeDisabled();
  // a coordinator hands the sign-off to another physician
  await page.getByLabel("Switch role").selectOption("coordinator");
  await page.getByLabel("New owner").selectOption({ label: "Dr. Layla Haddad" });
  await page.getByTestId("reassign").click();
  await expect(page.getByTestId("flash")).toContainText("Sign-off reassigned");
  // the new owner is a physician who did not counsel the patient
  await page.getByLabel("Switch role").selectOption("owner");
  await expect(page.getByTestId("sign-declined")).toBeEnabled();
  await page.getByTestId("sign-declined").click();
  await expect(page.getByTestId("flash")).toContainText("closed as declined");
  await expect(page.getByTestId("closed-declined")).toContainText("not a completed colonoscopy");
  await expect(page.getByTestId("re-offer-due")).toBeVisible();
  const types = await page.getByTestId("timeline").locator("li[data-event-type]").evaluateAll((els) => els.map((e) => e.getAttribute("data-event-type")));
  expect(types).toContain("record_informed_refusal");
  expect(types.at(-1)).toBe("close");
  expect(types).not.toContain("complete_confirmatory");
});

test("A34: the owner cancels the appointment and books it again; the case is back at booked", async ({ page }) => {
  await page.goto(`/coordinator/${BOOKED_CASE}`);
  await page.getByLabel("Switch role").selectOption("specialist");
  await page.getByTestId("cancel-reason").selectOption("patient_unwell");
  await page.getByTestId("cancel-appointment").click();
  await expect(page.getByTestId("flash")).toContainText("Appointment cancelled");
  await expect(page.getByTestId("book")).toBeVisible();
  await page.getByLabel("Appointment reference").fill("APPT-2026-0999");
  await page.getByTestId("book").click();
  await expect(page.getByTestId("flash")).toContainText("Booking recorded");
  await expect(page.getByTestId("cancel-appointment")).toBeVisible();
  const types = await page.getByTestId("timeline").locator("li[data-event-type]").evaluateAll((els) => els.map((e) => e.getAttribute("data-event-type")));
  expect(types.filter((t) => t === "book_confirmatory")).toHaveLength(2);
  expect(types).toContain("appointment_cancelled");
});
