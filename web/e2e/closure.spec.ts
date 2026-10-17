/**
 * C11: the closure flow end to end (T-C14, in English and Arabic), the consent freeze, the bounced handoff,
 * the Arabic patient flow and the dashboard's "nobody is looking" panel. All against the mock adapter.
 */
import { expect, test } from "@playwright/test";
import { BOUNCED_CASE, OVERDUE_CASE, PATIENT_CASE, WATCHDOG_CASE, freshData } from "./helpers";

test.beforeEach(({ page }) => freshData(page));

const SAMPLE = {
  en: { lang: "English", dir: "ltr", draft: /follow-up camera test called a colonoscopy/ },
  ar: { lang: "Arabic (العربية)", dir: "rtl", draft: /تنظير القولون/ },
} as const;

for (const lang of ["en", "ar"] as const) {
  test(`T-C14 (${lang}): overdue -> escalate -> request draft -> approve -> sent -> patient page -> sign-off -> closed`, async ({ page }) => {
    const s = SAMPLE[lang];
    // 1. Find the overdue case and see why it is overdue.
    await page.goto("/coordinator");
    const firstRow = page.getByTestId("worklist-row").first();
    await expect(firstRow).toHaveClass(/row-overdue/);
    await expect(firstRow).toContainText("23 days");
    await firstRow.getByRole("link").click();
    await expect(page).toHaveURL(new RegExp(OVERDUE_CASE));
    await expect(page.getByTestId("sla-clock")).toContainText("23 days overdue");
    await expect(page.getByTestId("timeline")).toContainText("SLA breached");
    await expect(page.getByTestId("timeline")).toContainText("Clinician review was due");

    // 2. Escalate.
    await page.getByTestId("escalate").click();
    await expect(page.getByTestId("flash")).toContainText("Escalated");

    // 3. Request a draft in the language under test. The coordinator can request, not approve.
    await page.getByLabel("Language").selectOption({ label: s.lang });
    await page.getByLabel("Channel").selectOption("sms");
    await page.getByTestId("request-draft").click();
    const draft = page.getByTestId(/^draft-d-mock-/);
    await expect(draft).toContainText("AI draft");
    await expect(draft).toContainText("Prompt hash");
    const text = draft.locator(`[lang="${lang}"]`);
    await expect(text).toContainText(s.draft);
    await expect(text).toHaveAttribute("dir", s.dir);
    await expect(draft.getByRole("button", { name: "Approve draft" })).toBeDisabled();
    await expect(page.getByTestId("decide-why")).toContainText("only the case owner can approve or reject");
    // The timeline shows the same text, loaded from GET /cases/{id}/drafts (the event carries none).
    await expect(page.getByTestId("timeline").locator(`blockquote[lang="${lang}"]`).last()).toContainText(s.draft);

    // 4. The case owner approves. The backend writes message_approved, then message_sent.
    await page.getByLabel("Switch role").selectOption("owner");
    await draft.getByRole("button", { name: "Approve draft" }).click();
    await expect(page.getByTestId("flash")).toContainText("Draft approved and sent.");
    await expect(page.getByTestId(/^approved-d-mock-/)).toContainText("Approved by Dr. Layla Haddad");
    await expect(page.getByTestId(/^sent-d-mock-/)).toContainText("Sent");
    const types = await page.getByTestId("timeline").locator("li[data-event-type]").evaluateAll((els) => els.map((e) => e.getAttribute("data-event-type")));
    expect(types.lastIndexOf("message_approved")).toBeGreaterThan(types.lastIndexOf("message_drafted"));
    expect(types.lastIndexOf("message_sent")).toBeGreaterThan(types.lastIndexOf("message_approved"));

    // 5. The patient's own page, in the language under test (no diagnosis wording).
    await page.goto(`/patient/${OVERDUE_CASE}`);
    // This patient last answered in Arabic, so the page opens in Arabic; the toggle picks the language under test.
    await page.getByTestId("patient-owner").waitFor();
    if ((await page.getByTestId("patient-root").getAttribute("lang")) !== lang) await page.locator(".lang-toggle").click();
    await expect(page.getByTestId("patient-root")).toHaveAttribute("dir", s.dir);
    await expect(page.getByTestId("patient-root")).toHaveAttribute("lang", lang);
    await expect(page.getByTestId("patient-owner")).toContainText("Dr. Layla Haddad");
    await expect(page.getByTestId("ack-done")).toBeVisible();

    // 6. The owner signs off with evidence: the only way to closed.
    await page.goto(`/coordinator/${OVERDUE_CASE}`);
    await expect(page.getByTestId("signoff")).toBeDisabled();
    await page.getByLabel("Evidence reference").fill("DiagnosticReport/colonoscopy-0001");
    await page.getByLabel("Signed by").fill("Dr. Layla Haddad");
    await page.getByTestId("signoff").click();
    await expect(page.getByTestId("flash")).toContainText("closed");
    await expect(page.locator("h1")).toContainText("Closed");
    await expect(page.getByTestId("timeline")).toContainText("Case closed (signed)");
    const close = page.getByTestId("timeline").locator('li[data-event-type="close"]');
    await expect(close).toContainText("primary_care_owner:Dr. Layla Haddad");
    await expect(close).toContainText("evidence ref: DiagnosticReport/colonoscopy-0001");
    await expect(close).toContainText("signed by: Dr. Layla Haddad");
    await expect(close.getByTestId("automated-chip")).toHaveCount(0); // a human closed it
  });
}

test("a rejected draft keeps a reason, writes nothing to the patient, and the template fallback stays available", async ({ page }) => {
  await page.goto(`/coordinator/${OVERDUE_CASE}`);
  await page.getByLabel("Switch role").selectOption("owner");
  await page.getByTestId("request-draft").click();
  const draft = page.getByTestId(/^draft-d-mock-/);
  await expect(draft.getByRole("button", { name: "Reject draft" })).toBeDisabled();
  await draft.getByLabel("Reason for rejecting").fill("Wrong tone for this patient");
  await draft.getByRole("button", { name: "Reject draft" }).click();
  await expect(page.getByTestId(/^rejected-d-mock-/)).toContainText("Rejected by Dr. Layla Haddad: Wrong tone for this patient");
  await expect(page.getByTestId("timeline").locator('li[data-event-type="message_approved"]')).toHaveCount(1); // only the seeded one
  await expect(page.getByTestId("timeline").locator('li[data-event-type="message_sent"]')).toHaveCount(1);
  // Rejecting does not stop the owner asking for another draft.
  await expect(page.getByTestId("request-draft")).toBeEnabled();
});

test("consent withdrawal freezes drafting and says why, on the coordinator's case page and worklist", async ({ page }) => {
  // The patient withdraws consent on their own page.
  await page.goto(`/patient/${PATIENT_CASE}`);
  await page.getByTestId("consent-withdraw").click();
  await expect(page.getByTestId("consent-withdrawn")).toContainText("You asked us to stop contact");

  // The coordinator sees the case frozen and flagged for review; drafting is disabled with the reason.
  await page.goto("/coordinator");
  const row = page.getByTestId("worklist-row").filter({ has: page.locator(`a[href$="${PATIENT_CASE}"]`) });
  await expect(row).toContainText("Consent frozen");
  await row.getByRole("link").click();
  await expect(page.getByTestId("consent-frozen-banner")).toContainText("flagged for human review");
  await expect(page.getByTestId("consent-state")).toContainText("Frozen");
  await expect(page.getByTestId("request-draft")).toBeDisabled();
  await expect(page.getByTestId("request-draft-why")).toContainText("consent is frozen");
  await expect(page.getByTestId("timeline")).toContainText("Flagged for human review (consent withdrawn)");

  // A human restores consent; drafting works again.
  await page.getByTestId("consent-restore").click();
  await expect(page.getByTestId("flash")).toContainText("restored consent");
  await expect(page.getByTestId("consent-frozen-banner")).toHaveCount(0);
  await expect(page.getByTestId("request-draft")).toBeEnabled();
});

test("a frozen draft cannot be approved by its owner, but can be rejected", async ({ page }) => {
  await page.goto(`/coordinator/${OVERDUE_CASE}`);
  await page.getByLabel("Switch role").selectOption("owner");
  await page.getByTestId("request-draft").click();
  const draft = page.getByTestId(/^draft-d-mock-/);
  await expect(draft).toBeVisible();
  // The patient withdraws while the draft waits for approval.
  await page.goto(`/patient/${OVERDUE_CASE}`);
  await page.getByTestId("consent-withdraw").click();
  await expect(page.getByTestId("consent-withdrawn")).toBeVisible();
  await page.goto(`/coordinator/${OVERDUE_CASE}`);
  await expect(page.getByTestId(/^approve-d-mock-\d+$/)).toBeDisabled();
  await expect(page.getByTestId(/^approve-d-mock-\d+-why$/)).toContainText("consent is frozen");
  await page.getByLabel("Reason for rejecting").fill("Patient withdrew consent");
  await expect(page.getByRole("button", { name: "Reject draft" })).toBeEnabled();
});

test("a bounced hand-off shows distinctly: red banner, timeline entry, worklist chip, and a re-route for the coordinator", async ({ page }) => {
  await page.goto("/coordinator");
  const row = page.getByTestId("worklist-row").filter({ has: page.locator(`a[href$="${BOUNCED_CASE}"]`) });
  await expect(row.getByTestId("bounced-chip")).toContainText("Hand-off bounced");
  await row.getByRole("link").click();
  const banner = page.getByTestId("handoff-bounced");
  await expect(banner).toContainText("Hand-off bounced: no response");
  await expect(banner).toContainText("Marina Digestive Health did not answer in 3 working days");
  await expect(page.getByTestId("rejections")).toHaveCount(0); // not an ordinary rejection
  await expect(page.getByTestId("tl-bounce")).toContainText("no response");
  await expect(page.getByTestId("tl-bounce").getByTestId("automated-chip")).toBeVisible();
  await expect(page.locator("h1")).toContainText("Referral rejected");
  // The coordinator re-routes it.
  await page.getByTestId("reassign").click();
  await expect(page.getByTestId("flash")).toContainText("re-routed");
  await expect(page.getByTestId("handoff-bounced")).toHaveCount(0);
  await expect(page.locator("h1")).toContainText("Owner accepted");
});

test("an ordinary rejection is labelled with its reason and is not called a bounce", async ({ page }) => {
  await page.goto(`/coordinator/${OVERDUE_CASE}`);
  await expect(page.getByTestId("rejections")).toContainText("No capacity");
  await expect(page.getByTestId("handoff-bounced")).toHaveCount(0);
});

test("a clinician who does not own the case is told to ask the owner to hand it off, and the receiving provider accepts another pending request", async ({ page }) => {
  await page.goto(`/coordinator/${OVERDUE_CASE}`);
  await page.getByLabel("Switch role").selectOption("clinician");
  await expect(page.getByTestId("handoff-not-owner")).toContainText("You do not own this case (the owner is Dr. Layla Haddad)");
  await expect(page.getByTestId("signoff")).toBeDisabled();
  await expect(page.getByTestId("signoff-why")).toContainText("do not own this case");
  await expect(page.getByTestId("hand-off")).toHaveCount(0);
  // Only the owner can request a hand-off (the API answers 403 to anyone else): no button, an instruction instead.
  await expect(page.getByTestId("request-my-provider")).toHaveCount(0);
  await expect(page.getByTestId("handoff-not-owner")).toContainText("Ask the current owner (Dr. Layla Haddad) to hand it off to Marina Digestive Health");
  // The clinician at the receiving provider accepts a different pending request and takes the case over.
  await page.getByLabel("Switch role").selectOption("specialist");
  await page.goto("/coordinator");
  const row = page.getByTestId("worklist-row").filter({ has: page.getByTestId("pending-handoff-chip") }).filter({ has: page.locator('a[href$="c0000016-0000-4000-8000-000000000016"]') });
  await row.getByRole("link").click();
  await page.getByTestId("accept-handoff").click();
  await expect(page.getByTestId("handoff-accepted")).toContainText("Corniche Gastro Unit");
  await expect(page.getByTestId("owner")).toContainText("Dr. Karim Nasser");
});

test("Arabic patient flow on a phone: RTL, no horizontal scroll, acknowledge, withdraw and resume consent", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/patient/${PATIENT_CASE}`);
  await page.getByRole("button", { name: /العربية/ }).click();
  const root = page.getByTestId("patient-root");
  await expect(root).toHaveAttribute("dir", "rtl");
  await expect(root).toHaveAttribute("lang", "ar");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("هذا لا يعني أنك مصاب بالسرطان")).toBeVisible();
  const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(await noOverflow()).toBe(true);
  // Acknowledge in Arabic.
  await page.getByRole("button", { name: "فهمت" }).click();
  await expect(page.getByTestId("ack-done")).toContainText("سجلنا أنك فهمت");
  // Withdraw and resume consent in Arabic.
  await page.getByRole("button", { name: "أوقفوا كل تواصل" }).click();
  await expect(page.getByTestId("consent-withdrawn")).toContainText("طلبت إيقاف التواصل");
  expect(await noOverflow()).toBe(true);
  await page.getByRole("button", { name: "يمكنكم التواصل معي مرة أخرى" }).click();
  await expect(page.getByTestId("consent-withdrawn")).toHaveCount(0);
  // The coordinator sees what the patient did, in the audit trail.
  await page.goto(`/coordinator/${PATIENT_CASE}`);
  const tl = page.getByTestId("timeline");
  await expect(tl).toContainText("Patient acknowledged");
  await expect(tl).toContainText("consent withdrawn");
  await expect(tl).toContainText("consent restored");
});

test("dashboard: cases nobody is looking at, and the stale-scheduler banner", async ({ page }) => {
  await page.goto("/dashboard");
  const rows = page.getByTestId("nobody-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Nobody is looking at this");
  await expect(rows.first()).toContainText("11 days");
  await expect(page.getByTestId("scheduler-banner")).toHaveCount(0);
  await page.getByTestId("toggle-scheduler").click();
  await expect(page.getByTestId("scheduler-banner")).toContainText("The scheduler is down");
  await expect(page.getByTestId("sim-badge")).toContainText("SIMULATED TIME:");
  await page.getByTestId("toggle-scheduler").click();
  await expect(page.getByTestId("scheduler-banner")).toHaveCount(0);
  await expect(page.getByTestId("scheduler-recovered")).toBeVisible();
  await rows.first().getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(WATCHDOG_CASE));
  await expect(page.getByTestId("watchdog-banner")).toBeVisible();
});
