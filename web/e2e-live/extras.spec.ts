/**
 * More of the real API through the browser: lost contact with failed attempts, a declined handoff that the
 * automation (A21) re-routes until its provider list is used up, and then the coordinator's re-route (`reassign.owner`
 * is the new owner's `role:user_id`, which only the API knows).
 */
import { expect, test, type Page } from "@playwright/test";
import { automationScan, createCase, driveToRerouteExhausted, getCase, login, stackProblem } from "./helpers";

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
  // A21: acknowledged and assigned (to Dr. Layla Haddad, the default owner of Riverside Primary Care) by system:automation.
  await page.getByTestId("owner").waitFor();
  const intake = (await getCase(caseId)).events as { type: string; actor: string }[];
  expect(intake.find((e) => e.type === "acknowledge")?.actor).toBe("system:automation");
  expect(intake.find((e) => e.type === "assign_ownership")?.actor).toBe("system:automation");
  await expect(page.getByTestId("acknowledge")).toHaveCount(0);
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

  // A21: a capacity rejection is re-routed by the automation, not by a person: it asks the next provider in
  // config/providers.yaml that was not asked yet (Corniche Gastro Unit), as handoff_requested from system:automation.
  await automationScan();
  const rerouted = await getCase(caseId);
  expect(rerouted.read_model.pending_handoff?.to_provider).toBe("Corniche Gastro Unit");
  expect(rerouted.events.filter((e: any) => e.type === "handoff_requested" && e.actor === "system:automation")).toHaveLength(1);
  // Nobody answers; the sweep bounces each request three working days later and the automation tries the next provider
  // until the list is used up. Only then is the case a coordinator's: the page shows the reason and the re-route panel.
  await driveToRerouteExhausted(caseId);
  const exhausted = await getCase(caseId);
  expect(exhausted.state).toBe("referral_rejected");
  expect(exhausted.read_model.review_reason).toBe("reroute_exhausted");

  // The coordinator re-routes to the specialist: the page sends that user's real actor string.
  await as(page, "coordinator", "Maryam Al Suwaidi");
  await page.reload();
  await page.getByTestId("reassign-panel").getByRole("combobox").selectOption({ label: "Dr. Karim Nasser" });
  await page.getByTestId("reassign").click();
  await expect(page.getByTestId("flash")).toContainText("re-routed");
  const karim = await login("specialist_owner", "Dr. Karim Nasser", { provider: "Corniche Gastro Unit" });
  const after = await getCase(caseId);
  expect(after.state).toBe("owner_accepted");
  expect(after.owner.actor).toBe(`specialist_owner:${karim.user_id}`);
  await expect(page.getByTestId("owner")).toContainText("Dr. Karim Nasser");
});
