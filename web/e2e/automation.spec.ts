/**
 * A21: automate every step that is not a human decision. The mock build runs with the automation actor ON (the shipped demo);
 * `?automation=off` (remembered for the tab) gives the manual flow. Humans act only at final decisions: the owner accepts,
 * a clinician signs off.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { freshData } from "./helpers";

const CLOSURE_CASE = "c0000017-0000-4000-8000-000000000017";
const EXHAUSTED_CASE = "c0000018-0000-4000-8000-000000000018";
const ASSIGNED_CASE = "c0000019-0000-4000-8000-000000000019";

test.beforeEach(({ page }) => freshData(page));

test("closure proposed: the banner shows the evidence, one click reaches a prefilled sign-off, and only the clinician's click closes", async ({ page }) => {
  await page.goto(`/coordinator/${CLOSURE_CASE}`);
  const banner = page.getByTestId("closure-proposal-banner");
  await expect(banner).toContainText("found the colonoscopy evidence");
  await expect(page.getByTestId("closure-proposal-evidence")).toHaveText("Procedure/colonoscopy-0017");
  // The counter, and the Automated chips on what the system did (and none on what people did).
  await expect(page.getByTestId("effort-counter")).toContainText("Human actions 5 / Automated actions 5");
  const row = (t: string) => page.getByTestId("timeline").locator(`li[data-event-type="${t}"]`);
  await expect(row("closure_proposed").getByTestId("automation-chip")).toBeVisible();
  await expect(row("complete_confirmatory").getByTestId("automation-chip")).toBeVisible();
  await expect(row("accept_ownership").getByTestId("automation-chip")).toHaveCount(0);

  // The coordinator cannot sign off: no button, and the reason is on the page.
  await expect(page.getByTestId("closure-proposal-review")).toHaveCount(0);
  await expect(page.getByTestId("closure-proposal-why")).toContainText("Only the case owner (a clinician) can sign off");
  await expect(page.getByTestId("signoff")).toBeDisabled();

  // The clinician who owns the case: one click to the sign-off panel, evidence already there, keyboard focus moved into it.
  await page.getByLabel("Switch role").selectOption("clinician");
  await page.getByTestId("closure-proposal-review").click();
  await expect(page.getByTestId("signoff-panel")).toBeFocused();
  await expect(page.getByLabel("Evidence reference")).toHaveValue("Procedure/colonoscopy-0017");
  await expect(page.getByTestId("signoff")).toBeDisabled(); // the signer is still to be named: closing is not automatic
  await expect(page.locator("h1")).toContainText("Confirmatory completed");
  await page.getByLabel("Signed by").fill("Dr. Huda Rahman");
  await page.getByTestId("signoff").click();
  await expect(page.getByTestId("flash")).toContainText("closed");
  await expect(page.locator("h1")).toContainText("Closed");
  await expect(page.getByTestId("closure-proposal-banner")).toHaveCount(0);
  const close = row("close");
  await expect(close).toContainText("primary_care_owner:Dr. Huda Rahman");
  await expect(close.getByTestId("automation-chip")).toHaveCount(0); // a person closed it
});

test("a clinician who does not own the case sees the proposal but cannot act on it", async ({ page }) => {
  await page.goto(`/coordinator/${CLOSURE_CASE}`);
  await page.getByLabel("Switch role").selectOption("owner"); // Dr. Layla; Dr. Huda owns this case
  await expect(page.getByTestId("closure-proposal-banner")).toBeVisible();
  await expect(page.getByTestId("closure-proposal-review")).toHaveCount(0);
  await expect(page.getByTestId("closure-proposal-why")).toContainText("do not own this case");
});

test("a new positive result is acknowledged and assigned by the system; the owner still accepts, and the template goes out only then", async ({ page }) => {
  await page.goto("/coordinator");
  await expect(page.getByTestId("automation-mode")).toContainText("automation on");
  await page.getByTestId("file-result").click();
  await expect(page.getByTestId("filed-note")).toContainText("acknowledged it and assigned an owner");
  await page.getByTestId("filed-note").getByRole("link").click();
  await expect(page.locator("h1")).toContainText("Acknowledged");
  const row = (t: string) => page.getByTestId("timeline").locator(`li[data-event-type="${t}"]`);
  await expect(row("acknowledge").getByTestId("automation-chip")).toBeVisible();
  await expect(row("assign_ownership").getByTestId("automation-chip")).toBeVisible();
  await expect(row("assign_ownership")).toContainText("Case assigned to an owner (not yet accepted)");
  await expect(page.getByTestId("owner")).toContainText("Dr. Layla Haddad");
  await expect(page.getByTestId("effort-counter")).toContainText("Human actions 0 / Automated actions 2");
  await expect(row("message_sent")).toHaveCount(0);
  // Acknowledge is gone (nothing left to do by hand); accepting is a person's click.
  await expect(page.getByTestId("acknowledge")).toHaveCount(0);
  await expect(page.getByTestId("accept-ownership")).toBeDisabled(); // the coordinator is not the owner
  await page.getByLabel("Switch role").selectOption("owner");
  await page.getByTestId("accept-ownership").click();
  await expect(page.getByTestId("flash")).toContainText("You now own this case");
  await expect(page.locator("h1")).toContainText("Owner accepted");
  await expect(row("message_sent")).toContainText("Template message sent to the patient");
  await expect(row("message_sent").getByTestId("automation-chip")).toBeVisible();
  await expect(page.getByTestId("effort-counter")).toContainText("Human actions 1 / Automated actions 3");
});

test("with ?automation=off the flow is manual: no chips, the coordinator acknowledges", async ({ page }) => {
  await page.goto("/coordinator?automation=off");
  await expect(page.getByTestId("automation-mode")).toContainText("automation off");
  await page.getByTestId("file-result").click();
  await page.getByTestId("filed-note").getByRole("link").click();
  await expect(page.locator("h1")).toContainText("Result received");
  await expect(page.getByTestId("automation-chip")).toHaveCount(0);
  await page.getByTestId("acknowledge").click();
  await expect(page.getByTestId("flash")).toContainText("acknowledged");
  await expect(page.getByTestId("automation-chip")).toHaveCount(0);
  // The choice is remembered for the tab, and the plain seed has none of the automation demo cases (the case filed above took the first free id).
  await page.goto(`/coordinator/${ASSIGNED_CASE}`);
  await expect(page.getByTestId("automation-mode")).toContainText("automation off");
  await expect(page.getByText("Case not found.")).toBeVisible();
});

test("an exhausted reroute list is flagged for a coordinator, who reassigns", async ({ page }) => {
  await page.goto(`/coordinator/${EXHAUSTED_CASE}`);
  await expect(page.getByTestId("reroute-exhausted-banner")).toContainText("Reroute list exhausted: a coordinator must reassign");
  const review = page.getByTestId("timeline").locator('li[data-event-type="review_requested"]');
  await expect(review).toContainText("Reroute list exhausted: a coordinator must reassign");
  await expect(review.getByTestId("automation-chip")).toBeVisible();
  await page.getByTestId("reassign").click();
  await expect(page.getByTestId("flash")).toContainText("re-routed");
  await expect(page.getByTestId("reroute-exhausted-banner")).toHaveCount(0);
});

test("an assigned case waits for its owner, and anyone else is told who must accept", async ({ page }) => {
  await page.goto(`/coordinator/${ASSIGNED_CASE}`);
  await page.getByLabel("Switch role").selectOption("clinician");
  await expect(page.getByTestId("assigned-note")).toContainText("Assigned to Dr. Layla Haddad");
  await expect(page.getByTestId("accept-ownership")).toBeDisabled();
  await expect(page.getByTestId("accept-ownership-why")).toContainText("must accept it");
});

test("dashboard: the human-effort card shows the reduction against the 40% target, and says the minutes are assumed", async ({ page }) => {
  await page.goto("/dashboard");
  const card = page.getByTestId("effort-card");
  await expect(card).toContainText("Human effort");
  await expect(page.getByTestId("effort-reduction")).toContainText("%");
  await expect(page.getByTestId("effort-target")).toContainText("Target: 40%");
  await expect(page.getByTestId("effort-target-status")).toBeVisible();
  await expect(page.getByTestId("effort-total-automated")).not.toHaveText("0");
  await expect(page.getByTestId("effort-caveat")).toContainText("Assumed minutes, to source");
  await page.getByLabel("Switch role").selectOption("owner");
  await expect(page.getByTestId("effort-card")).toHaveCount(0);
});

for (const scheme of ["light", "dark"] as const) {
  test(`axe (${scheme}): the closure banner, the exhausted-list banner and the effort card`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    const audit = async (label: string) => {
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${v.id}: ${v.help}`), `axe on ${label} (${scheme})`).toEqual([]);
    };
    await page.goto(`/coordinator/${CLOSURE_CASE}`);
    await page.getByLabel("Switch role").selectOption("clinician");
    await page.getByTestId("closure-proposal-review").waitFor();
    await audit("closure banner");
    await page.goto(`/coordinator/${EXHAUSTED_CASE}`);
    await page.getByLabel("Switch role").selectOption("coordinator");
    await page.getByTestId("reroute-exhausted-banner").waitFor();
    await audit("exhausted reroute list");
    await page.goto("/dashboard");
    await page.getByTestId("effort-card").waitFor();
    await audit("dashboard effort card");
  });
}
