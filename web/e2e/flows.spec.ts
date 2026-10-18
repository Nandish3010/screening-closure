import { expect, test } from "@playwright/test";

const OVERDUE_CASE = "c0000001-0000-4000-8000-000000000001";
const PATIENT_CASE = "c0000007-0000-4000-8000-000000000007";

test.beforeEach(async ({ page }) => {
  // Fresh mock data for every test (the mock persists to localStorage).
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("e2e-init")) {
      localStorage.clear();
      sessionStorage.setItem("e2e-init", "1");
    }
  });
});

test("coordinator escalates and requests a draft; the owner approves it; the owner signs off", async ({ page }) => {
  await page.goto("/coordinator");
  await expect(page.getByTestId("sim-badge")).toContainText("SIMULATED TIME: 2026-11-04");

  // The most overdue case is first.
  const firstRow = page.getByTestId("worklist-row").first();
  await expect(firstRow).toHaveClass(/row-overdue/);
  await expect(firstRow).toContainText("23 days");
  await firstRow.getByRole("link").click();

  await expect(page).toHaveURL(new RegExp(OVERDUE_CASE));
  await expect(page.getByTestId("sla-clock")).toContainText("overdue");
  await expect(page.getByTestId("timeline")).toContainText("SLA breached");
  await expect(page.getByTestId("rejections")).toContainText("No capacity");
  // Automated actors carry a chip; the scheduler marked this case overdue.
  await expect(page.getByTestId("timeline").locator('li[data-event-type="mark_overdue"]').getByTestId("automated-chip")).toBeVisible();

  // Escalate
  await page.getByTestId("escalate").click();
  await expect(page.getByTestId("flash")).toContainText("Escalated");
  await expect(page.getByTestId("timeline")).toContainText("Escalated");

  // The coordinator can request a draft but not approve it.
  await page.getByTestId("request-draft").click();
  const draft = page.getByTestId(/^draft-d-mock-/);
  await expect(draft).toContainText("AI draft");
  await expect(draft.getByRole("button", { name: "Approve draft" })).toBeDisabled();

  // The case owner approves; the backend sends only after the approval (message_approved, then message_sent).
  await page.getByLabel("Switch role").selectOption("owner");
  await draft.getByRole("button", { name: "Approve draft" }).click();
  await expect(page.getByTestId(/^approved-d-mock-/)).toContainText("Approved by Dr. Layla Haddad");
  await expect(page.getByTestId(/^sent-d-mock-/)).toContainText("Sent");
  await expect(page.getByTestId("timeline")).toContainText("Message sent to the patient");

  // Sign-off needs evidence and a signer.
  await expect(page.getByTestId("signoff")).toBeDisabled();
  await page.getByLabel("Evidence reference").fill("DiagnosticReport/colonoscopy-0001");
  await expect(page.getByTestId("signoff")).toBeDisabled();
  await page.getByLabel("Signed by").fill("Dr. Layla Haddad");
  await expect(page.getByTestId("signoff")).toBeEnabled();
  await page.getByTestId("signoff").click();

  await expect(page.getByTestId("flash")).toContainText("closed");
  await expect(page.locator("h1")).toContainText("Closed");
  await expect(page.getByTestId("timeline")).toContainText("Case closed (signed)");
  // Back on the worklist the case is gone from "open".
  await page.getByRole("link", { name: /Worklist/ }).click();
  await expect(page.getByTestId("worklist-row").first()).not.toContainText("c0000001");
});

test("watchdog: the worklist and the case page say nobody is looking at it", async ({ page }) => {
  await page.goto("/coordinator");
  const flagged = page.getByTestId("worklist-row").filter({ has: page.getByTestId("watchdog-marker") });
  await expect(flagged).toHaveCount(1);
  await expect(flagged).toContainText("Nobody is looking at this");
  await flagged.getByRole("link").click();
  await expect(page.getByTestId("watchdog-banner")).toContainText("Nobody is looking at this");
  const item = page.getByTestId("timeline").locator('li[data-kind="watchdog"]');
  await expect(item).toHaveClass(/tl-watchdog/);
  await expect(item.getByTestId("automated-chip")).toBeVisible();
  // A human acting on the case clears the flag.
  await page.getByTestId("accept-ownership").or(page.getByTestId("mark-lost-contact")).first().click();
  await expect(page.getByTestId("watchdog-banner")).toHaveCount(0);
});

test("reopen: only a clinician can reopen a closed case, with a reason", async ({ page }) => {
  await page.goto("/coordinator");
  await page.getByLabel("State").selectOption("closed");
  await page.getByTestId("worklist-row").first().getByRole("link").click();
  await expect(page.locator("h1")).toContainText("Closed");
  await expect(page.getByTestId("reopen")).toBeDisabled();
  await expect(page.getByTestId("reopen-why")).toContainText("clinician");
  await page.getByLabel("Switch role").selectOption("clinician");
  await expect(page.getByTestId("reopen-why")).toHaveCount(0); // the reason is a taxonomy select with a default
  await page.getByLabel("Reason for reopening").selectOption("new_finding");
  await expect(page.getByTestId("reopen")).toBeEnabled();
  await page.getByTestId("reopen").click();
  await expect(page.getByTestId("flash")).toContainText("reopened");
  await expect(page.locator("h1")).toContainText("Reopened");
  await expect(page.getByTestId("timeline")).toContainText("Case reopened by a clinician");
});

test("patient: acknowledge, Arabic RTL, call-back request, consent", async ({ page }) => {
  await page.goto(`/patient/${PATIENT_CASE}`);
  await expect(page.getByTestId("sim-badge")).toBeVisible();
  await expect(page.getByText("Your care team will explain your result in person or by phone.")).toBeVisible();
  await expect(page.getByTestId("patient-owner")).toContainText("Sara Al Mansoori");

  // Arabic
  await page.getByRole("button", { name: /العربية/ }).click();
  await expect(page.getByTestId("patient-root")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("سيشرح لك فريق رعايتك نتيجتك وجهاً لوجه أو عبر الهاتف.")).toBeVisible();
  await page.getByRole("button", { name: /English/ }).click();
  await expect(page.getByTestId("patient-root")).toHaveAttribute("dir", "ltr");

  // Consent
  await page.getByRole("checkbox", { name: "Text message" }).uncheck();
  await expect(page.getByText("Saved")).toBeVisible();

  // "I don't understand" asks for a call-back
  await page.getByRole("button", { name: "I don't understand this" }).click();
  await expect(page.getByTestId("callback-done")).toBeVisible();

  // Acknowledge
  await page.getByRole("button", { name: "I understand, please contact me" }).click();
  await expect(page.getByTestId("ack-done")).toBeVisible();

  // The coordinator now sees the call-back and the consent on the case.
  await page.goto(`/coordinator/${PATIENT_CASE}`);
  await expect(page.getByTestId("callback-banner")).toBeVisible();
  await expect(page.getByTestId("consent-state")).toContainText("WhatsApp");
  await expect(page.getByTestId("consent-state")).not.toContainText("text messages");
  await expect(page.getByTestId("timeline")).toContainText("Patient acknowledged");
});

test("dashboard: sim-time badge and metrics render; badge is on every page", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByTestId("kpi-p50")).toContainText("38");
  await expect(page.getByTestId("kpi-p90")).toContainText("79");
  await expect(page.getByTestId("chart-Open cases by age")).toBeVisible();
  await expect(page.getByTestId("chart-SLA breaches by provider")).toBeVisible();
  for (const path of ["/", "/coordinator", "/dashboard", `/patient/${PATIENT_CASE}`]) {
    await page.goto(path);
    await expect(page.getByTestId("sim-badge")).toContainText("SIMULATED TIME:");
  }
});

test("pwa: manifest and service worker are served; patient page works offline after first load", async ({ page, context }) => {
  const manifest = await page.request.get("/manifest.webmanifest");
  expect(manifest.ok()).toBe(true);
  expect((await manifest.json()).start_url).toBe("/patient");

  await page.goto(`/patient/${PATIENT_CASE}`);
  await expect(page.getByText("Your care team will explain your result in person or by phone.")).toBeVisible();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  // Reload once so the page is controlled by the service worker, then go offline.
  await page.reload();
  await expect(page.getByText("Your care team will explain your result in person or by phone.")).toBeVisible();
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByText("Your care team will explain your result in person or by phone.")).toBeVisible();
  await context.setOffline(false);
});
