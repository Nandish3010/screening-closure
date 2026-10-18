/**
 * More of the real API through the browser: lost contact with failed attempts, a declined handoff and the
 * coordinator's re-route (`reassign.owner` is the new owner's `role:user_id`, which only the API knows).
 */
import { expect, test, type Page } from "@playwright/test";
import { createCase, getCase, login, stackProblem } from "./helpers";

let problem: string | null = null;
test.beforeAll(async () => {
  problem = await stackProblem();
});

async function as(page: Page, role: string, name: string) {
  await page.getByLabel("Switch role").selectOption(role);
  await expect(page.locator(".who")).toHaveText(name);
}

test("lost contact, a declined handoff and the coordinator's re-route", async ({ page }) => {
  test.skip(problem !== null, `live e2e skipped: ${problem}`);
  const { caseId } = await createCase();
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("live-e2e-init")) {
      localStorage.clear();
      sessionStorage.setItem("live-e2e-init", "1");
    }
  });
  await page.goto(`/coordinator/${caseId}`);
  // A21: the automation actor has normally acknowledged the new case already; the manual acknowledge stays for the older flow.
  await page.getByTestId("owner").waitFor();
  if (await page.getByTestId("acknowledge").count()) {
    await page.getByTestId("acknowledge").click();
    await expect(page.getByTestId("flash")).toContainText("acknowledged");
  }
  await as(page, "owner", "Dr. Layla Haddad");
  await page.getByTestId("accept-ownership").click();
  await expect(page.getByTestId("flash")).toContainText("You now own this case");
  await page.getByTestId("contact-patient").click();
  await expect(page.getByTestId("flash")).toContainText("Patient contact recorded");

  // Lost contact needs the failed attempts the pathway asks for (3): the body carries `failed_attempts`.
  await as(page, "coordinator", "Maryam Al Suwaidi");
  await page.getByTestId("mark-lost-contact").click();
  await expect(page.getByTestId("flash")).toContainText("Marked as lost contact");
  expect((await getCase(caseId)).state).toBe("lost_contact");
  await as(page, "owner", "Dr. Layla Haddad");
  await page.getByTestId("contact-patient").click(); // reaching the patient again clears it
  await expect(page.getByTestId("flash")).toContainText("Patient contact recorded");

  // The patient acknowledges on their own page (patient_ref read from the case).
  await as(page, "patient", "Patient");
  await page.getByRole("button", { name: /I understand/ }).click();
  await expect(page.getByTestId("ack-done")).toBeVisible();

  // The owner asks Marina Digestive Health; their clinician declines for capacity: the case is back with the referrer.
  await as(page, "owner", "Dr. Layla Haddad");
  await page.getByTestId("handoff-provider").selectOption("Marina Digestive Health");
  await page.getByTestId("hand-off").click();
  await expect(page.getByTestId("handoff-pending")).toContainText("Marina Digestive Health");
  await as(page, "clinician", "Dr. Huda Rahman");
  await page.getByTestId("reject-handoff").click();
  await expect(page.getByTestId("flash")).toContainText("Hand-off declined");
  expect((await getCase(caseId)).state).toBe("referral_rejected");

  // The coordinator re-routes to the specialist: the page sends that user's real actor string.
  await as(page, "coordinator", "Maryam Al Suwaidi");
  await page.getByTestId("reassign-panel").getByRole("combobox").selectOption({ label: "Dr. Karim Nasser" });
  await page.getByTestId("reassign").click();
  await expect(page.getByTestId("flash")).toContainText("re-routed");
  const karim = await login("specialist_owner", "Dr. Karim Nasser", { provider: "Corniche Gastro Unit" });
  const after = await getCase(caseId);
  expect(after.state).toBe("owner_accepted");
  expect(after.owner.actor).toBe(`specialist_owner:${karim.user_id}`);
  await expect(page.getByTestId("owner")).toContainText("Dr. Karim Nasser");
});
