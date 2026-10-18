/**
 * The full flow through the browser against the REAL stack (http adapter): worklist, accept ownership,
 * contact, patient acknowledges, draft approved and sent, handoff to a specialist, booking, clock advance with
 * overdue and escalation, colonoscopy result recorded in HAPI, clinician review, close with a real Procedure and
 * the signer's own Practitioner, reopen with a taxonomy reason. The automation (A21) is on: the new case is
 * acknowledged and assigned by system:automation, and the closure is proposed (never closed) by the evidence scan once the
 * appointment date has passed and the colonoscopy is in HAPI.
 *
 * It creates its own case (for a seeded patient) and leaves the simulation clock later than it found it:
 * run `make demo-reset` afterwards to put the demo back.
 */
import { expect, test, type Page } from "@playwright/test";
import { advanceClock, automationScan, createCase, evidenceScan, fhirExists, getCase, login, call, stackProblem, sweep } from "./helpers";

let problem: string | null = null;
test.beforeAll(async () => {
  problem = await stackProblem();
});

async function as(page: Page, role: string, name: string) {
  await page.getByLabel("Switch role").selectOption(role);
  await expect(page.locator(".who")).toHaveText(name);
}

test("worklist to reopen against the real API", async ({ page }) => {
  test.skip(problem !== null, `live e2e skipped: ${problem}`);
  const { caseId } = await createCase();
  const events = () => getCase(caseId).then((c) => c.events as { type: string; actor: string; payload: any }[]);

  // 1. Coordinator: the seeded worklist, then the new case.
  // Fresh browser storage once per test (an init script also runs on reload, which must keep the chosen role).
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("live-e2e-init")) {
      localStorage.clear();
      sessionStorage.setItem("live-e2e-init", "1");
    }
  });
  await page.goto("/coordinator");
  await expect(page.locator(".adapter")).toHaveText("http");
  await expect(page.getByTestId("worklist-row").first()).toBeVisible();
  expect(await page.getByTestId("worklist-row").count()).toBeGreaterThanOrEqual(22);
  await expect(page.getByTestId("sim-badge")).toContainText("SIMULATED TIME");
  await page.goto(`/coordinator/${caseId}`);
  // A21: nobody acknowledges or assigns: system:automation did both when the case was created, and named the default owner
  // of the provider the patient is registered with (no registration was sent: the default, Riverside Primary Care).
  await page.getByTestId("owner").waitFor();
  const intake = await events();
  expect(intake.find((e) => e.type === "acknowledge")).toMatchObject({ actor: "system:automation", payload: { auto: true } });
  expect(intake.find((e) => e.type === "assign_ownership")).toMatchObject({
    actor: "system:automation", payload: { auto: true, provider: "Riverside Primary Care" },
  });
  expect((await getCase(caseId)).state).toBe("acknowledged");
  await expect(page.getByTestId("acknowledge")).toHaveCount(0);

  // 2. The named owner accepts ownership: the body carries no owner (the API takes the caller).
  await as(page, "owner", "Dr. Layla Haddad");
  await page.getByTestId("accept-ownership").click();
  await expect(page.getByTestId("flash")).toContainText("You now own this case");
  await expect(page.getByTestId("owner")).toContainText("Dr. Layla Haddad");
  expect((await events()).find((e) => e.type === "accept_ownership")?.payload).toEqual({});

  // A clinician who is not the owner cannot request a handoff: the page says to ask the owner.
  await as(page, "clinician", "Dr. Huda Rahman");
  await expect(page.getByTestId("handoff-not-owner")).toContainText("Ask the current owner (Dr. Layla Haddad)");
  await expect(page.getByTestId("request-my-provider")).toHaveCount(0);
  await as(page, "owner", "Dr. Layla Haddad");

  // 3. The owner requests a draft in Arabic and approves it: Sent.
  await page.getByLabel("Language").selectOption("ar");
  await page.getByTestId("request-draft").click();
  const draft = page.getByTestId(/^draft-[0-9a-f-]{36}$/).first();
  await expect(draft).toContainText("AI draft");
  await draft.getByRole("button", { name: "Approve draft" }).click();
  await expect(page.getByTestId(/^sent-/).first()).toContainText("Sent");

  // 4. Contact the patient (the approved draft is linked), then the patient acknowledges on their page.
  await page.getByTestId("contact-patient").click();
  await expect(page.getByTestId("flash")).toContainText("Patient contact recorded");
  await as(page, "patient", "Patient");
  await expect(page).toHaveURL(new RegExp(`/patient/${caseId}`));
  await page.getByRole("button", { name: /I understand/ }).click();
  await expect(page.getByTestId("ack-done")).toBeVisible();

  // 5. Owner requests a handoff to a specialist provider; the specialist accepts.
  await as(page, "owner", "Dr. Layla Haddad");
  await page.getByTestId("handoff-provider").selectOption("Corniche Gastro Unit");
  await page.getByTestId("hand-off").click();
  await expect(page.getByTestId("handoff-pending")).toContainText("Corniche Gastro Unit");
  await as(page, "specialist", "Dr. Karim Nasser");
  await page.getByTestId("accept-handoff").click();
  await expect(page.getByTestId("flash")).toContainText("you now own this case");
  await expect(page.getByTestId("owner")).toContainText("Dr. Karim Nasser");

  // 6. Book the confirmatory test with an appointment date.
  await page.getByLabel("Appointment reference").fill("Appointment/live-e2e-1");
  await page.getByLabel(/Appointment date/).fill("2026-03-02");
  await page.getByTestId("book").click();
  await expect(page.getByTestId("flash")).toContainText("Booking recorded");
  expect((await events()).find((e) => e.type === "book_confirmatory")?.payload).toMatchObject({ appointment_date: "2026-03-02" });

  // 7. A supervisor moves the clock past the 31 working days; the sweep marks the case overdue and escalates. Since A18
  // (ADR-028) the colonoscopy clock is the confirmatory_completed milestone, so the breach is a milestone breach.
  await advanceClock(60);
  await sweep();
  await automationScan();
  // The booked date (2 March) is behind the clock now, so the evidence scan asks HAPI: nothing there yet, so no closure proposal,
  // and the scheduler container's own scans will not ask again for six simulated hours (which keeps them out of step 8).
  await evidenceScan();
  expect((await getCase(caseId)).read_model.closure_proposal).toBeNull();
  await page.reload();
  await expect(page.getByTestId("sla-clock")).toContainText("overdue");
  await expect(page.getByTestId("escalation-level")).toContainText(/Level [1-3] of 3/);
  await expect(page.getByTestId("timeline")).toContainText("Milestone missed");
  await expect(page.getByTestId("timeline")).toContainText("Colonoscopy within 31 working days of referral was due");
  // The coordinator (target of the last rung) accepts the escalation.
  await as(page, "coordinator", "Maryam Al Suwaidi");
  await page.getByTestId("accept-escalation").click();
  await expect(page.getByTestId("flash")).toContainText("Escalation accepted");
  await as(page, "specialist", "Dr. Karim Nasser");

  // 8. The specialist records the colonoscopy in HAPI and completes the confirmatory test with its reference.
  await page.getByTestId("record-colonoscopy").click();
  await expect(page.getByTestId("flash")).toContainText(`Procedure/colonoscopy-${caseId}`);
  await page.getByTestId("record-result").click();
  await expect(page.getByTestId("flash")).toContainText("Confirmatory result recorded");
  expect(await fhirExists(`Procedure/colonoscopy-${caseId}`)).toBe(true);

  // The colonoscopy is recorded and the step completed by a person; the evidence scan proposes the closure once its
  // six-hour memory of "no colonoscopy" has passed: move the clock a day (whole hours, as a supervisor does) and run it.
  expect((await getCase(caseId)).read_model.closure_proposal).toBeNull();
  await advanceClock(1);
  await sweep();
  await evidenceScan();
  const proposal = (await getCase(caseId)).read_model.closure_proposal;
  expect(proposal).toMatchObject({ evidence_ref: `Procedure/colonoscopy-${caseId}` });
  expect((await events()).find((e) => e.type === "closure_proposed")).toMatchObject({ actor: "system:automation", payload: { auto: true } });
  expect((await getCase(caseId)).state).toBe("confirmatory_completed"); // proposed, not closed
  await page.reload();
  await expect(page.getByTestId("closure-proposal-evidence")).toHaveText(`Procedure/colonoscopy-${caseId}`);

  // 9. Sign-off: the evidence is the Procedure, the signer the caller's own Practitioner. Review, then close.
  const me = await login("specialist_owner", "Dr. Karim Nasser", { provider: "Corniche Gastro Unit" });
  await expect(page.getByLabel("Evidence reference")).toHaveValue(`Procedure/colonoscopy-${caseId}`);
  await expect(page.getByTestId("signer")).toHaveValue(`Practitioner/${me.user_id}`);
  await page.getByTestId("signoff").click();
  await expect(page.getByTestId("flash")).toContainText("Case closed");
  await expect(page.locator("h1")).toContainText("Closed");
  const closed = (await events()).find((e) => e.type === "close")!;
  expect(closed.payload).toEqual({ evidence_ref: `Procedure/colonoscopy-${caseId}`, signed_by: `Practitioner/${me.user_id}` });
  expect(closed.actor).toBe(`specialist_owner:${me.user_id}`);

  // 10. Reopen with a reason from the taxonomy.
  await page.getByLabel("Reason for reopening").selectOption("closed_in_error");
  await page.getByTestId("reopen").click();
  await expect(page.getByTestId("flash")).toContainText("Case reopened");
  const reopened = (await events()).find((e) => e.type === "reopen")!;
  expect(reopened.payload).toEqual({ reason: "closed_in_error" });
  expect((await getCase(caseId)).state).toBe("reopened");

  // The owner scoping is the API's, not just the page's: another clinician is refused with 403.
  const stranger = await login("primary_care_owner", "Dr. Huda Rahman", { provider: "Marina Digestive Health" });
  const refused = await call(stranger.token, "POST", `/cases/${caseId}/events`, { type: "handoff_requested", payload: { to_provider: "Marina Digestive Health" } });
  expect(refused.status).toBe(403);
});
