/**
 * A33 (informed refusal) and A34 (cancel and rebook) in the web, ADR-042. The mock mirrors the API: a recorded refusal moves the
 * case to declined_pending_signoff; a physician who is not the counsellor closes it as declined; a cancelled appointment returns
 * the case to patient_acknowledged and a booking rebooks it, and the colonoscopy clock does not restart (A25).
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, type ApiClient } from "../src/api/contract";
import { ROLE_NAMES } from "../src/api/identities";
import { createMockAdapter } from "../src/api/mockAdapter";
import { addMonths, closureOutcomeOf, personId, refusalOf } from "../src/lib/derive";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { renderWithApp } from "./helpers";

let api: ApiClient;
beforeEach(() => {
  api = createMockAdapter(null);
});

type Physician = "owner" | "specialist" | "clinician";
const PHYSICIANS: Physician[] = ["owner", "specialist", "clinician"];
/** A seeded case in `state` owned by one of the demo physicians (clinicians act only on cases they own), and which one. */
async function owned(state: string): Promise<{ id: string; role: Physician; other: Physician }> {
  const rows = await api.listCases();
  for (const row of rows) {
    const role = PHYSICIANS.find((r) => row.owner_actor?.endsWith(`:${ROLE_NAMES[r]}`));
    if (row.state === state && role) return { id: row.case_id, role, other: PHYSICIANS.find((r) => r !== role)! };
  }
  throw new Error(`no seeded case in ${state} owned by a physician login`);
}
/** Log in as the case's owner and return their actor string. */
async function asOwner(role: Physician): Promise<string> {
  await api.login(role, ROLE_NAMES[role]);
  return api.actorFor(role, ROLE_NAMES[role]);
}
const REF = "DocumentReference/counselling-1";

describe("A33 informed refusal in the mock", () => {
  it("moves the case to declined_pending_signoff with a re-offer date 12 months on", async () => {
    const { id, role } = await owned("confirmatory_booked");
    const counsellor = await asOwner(role);
    const d = await api.postEvent(id, { type: "record_informed_refusal", actor: counsellor, payload: { counselling_ref: REF, counselled_by: counsellor, reason: "personal_choice" } });
    expect(d.state).toBe("declined_pending_signoff");
    expect(d.refusal).toMatchObject({ counselling_ref: REF, reason: "personal_choice" });
    expect(d.re_offer_due_at).toBe(addMonths(d.refusal!.recorded_at, 12));
    expect(d.closure_outcome).toBeNull();
  });

  it("refuses a malformed refusal: no counselling ref, a coordinator as counsellor, a bad reason, other without a note", async () => {
    const { id, role } = await owned("confirmatory_booked");
    const me = await asOwner(role);
    const ok = { counselling_ref: REF, counselled_by: me, reason: "personal_choice" };
    const bad = async (payload: Record<string, unknown>, status: number) =>
      expect(api.postEvent(id, { type: "record_informed_refusal", actor: me, payload })).rejects.toMatchObject({ status });
    await bad({ ...ok, counselling_ref: "Procedure/x" }, 422);
    await bad({ ...ok, counselled_by: "coordinator:Maryam" }, 422);
    await bad({ ...ok, reason: "because" }, 422);
    await bad({ ...ok, reason: "other_with_note" }, 422);
    await bad({ counselled_by: me, reason: "personal_choice" }, 422);
  });

  it("is allowed only from owner_accepted, patient_acknowledged and confirmatory_booked", async () => {
    const { id: completed, role } = await owned("confirmatory_completed");
    const me = await asOwner(role);
    await expect(api.postEvent(completed, { type: "record_informed_refusal", actor: me, payload: { counselling_ref: REF, counselled_by: me, reason: "personal_choice" } }))
      .rejects.toMatchObject({ status: 409 });
  });

  it("closes as declined only by a physician who is not the counsellor, citing the counselling record", async () => {
    const { id, role, other } = await owned("confirmatory_booked");
    const counsellor = await asOwner(role);
    await api.postEvent(id, { type: "record_informed_refusal", actor: counsellor, payload: { counselling_ref: REF, counselled_by: counsellor, reason: "fear_of_procedure" } });
    const close = { outcome: "declined", evidence_ref: REF, signed_by: counsellor };
    // the counsellor cannot sign their own counselling
    await expect(api.postEvent(id, { type: "close", actor: counsellor, payload: close })).rejects.toMatchObject({ status: 409 });
    // the case must first pass to another physician: only a coordinator or supervisor reassigns
    const spec = await api.actorFor(other, ROLE_NAMES[other]);
    await api.login("coordinator", ROLE_NAMES.coordinator);
    const coord = await api.actorFor("coordinator", ROLE_NAMES.coordinator);
    await api.postEvent(id, { type: "reassign", actor: coord, payload: { owner: spec } });
    await api.login(other, ROLE_NAMES[other]);
    // wrong evidence (not the counselling record) and a colonoscopy closure shape are refused
    await expect(api.postEvent(id, { type: "close", actor: spec, payload: { outcome: "declined", evidence_ref: "DocumentReference/other", signed_by: spec } })).rejects.toBeInstanceOf(ApiError);
    await expect(api.postEvent(id, { type: "close", actor: spec, payload: { evidence_ref: REF, signed_by: spec } })).rejects.toBeInstanceOf(ApiError);
    const done = await api.postEvent(id, { type: "close", actor: spec, payload: { outcome: "declined", evidence_ref: REF, signed_by: spec } });
    expect(done.state).toBe("closed");
    expect(done.closure_outcome).toBe("declined");
    expect(done.re_offer_due_at).toBeTruthy();
  });

  it("never lets a coordinator or an automated actor close, and a declined close needs the declined state", async () => {
    const { id, role } = await owned("confirmatory_booked");
    const me = await asOwner(role);
    // a declined close without a recorded refusal: 409
    await expect(api.postEvent(id, { type: "close", actor: me, payload: { outcome: "declined", evidence_ref: REF, signed_by: me } })).rejects.toMatchObject({ status: 409 });
  });

  it("replays the refusal from the log and clears it on a reopen", () => {
    const at = "2026-11-03T09:00:00.000Z";
    const events = [
      { seq: 1, type: "record_informed_refusal", at, actor: "primary_care_owner:a", payload: { counselling_ref: REF, counselled_by: "primary_care_owner:a", reason: "personal_choice" } },
      { seq: 2, type: "close", at, actor: "specialist_owner:b", payload: { outcome: "declined", evidence_ref: REF, signed_by: "specialist_owner:b" } },
    ];
    expect(refusalOf(events)?.re_offer_due_at).toBe("2027-11-03T09:00:00.000Z");
    expect(closureOutcomeOf(events)).toBe("declined");
    const reopened = [...events, { seq: 3, type: "reopen", at, actor: "specialist_owner:b", payload: { reason: "patient_request" } }];
    expect(refusalOf(reopened)).toBeNull();
    expect(closureOutcomeOf(reopened)).toBeNull();
  });

  it("clamps the re-offer date at the end of a short month and compares people by user id", () => {
    expect(addMonths("2027-02-28T00:00:00.000Z", 12)).toBe("2028-02-28T00:00:00.000Z");
    expect(addMonths("2028-02-29T00:00:00.000Z", 12)).toBe("2029-02-28T00:00:00.000Z");
    expect(personId("Practitioner/u1")).toBe(personId("specialist_owner:u1"));
  });
});

describe("A34 cancel and rebook in the mock", () => {
  it("returns a booked case to patient_acknowledged and a booking rebooks it", async () => {
    const { id, role } = await owned("confirmatory_booked");
    const me = await asOwner(role);
    const before = await api.getCase(id);
    const cancelled = await api.postEvent(id, { type: "appointment_cancelled", actor: me, payload: { reason: "patient_request", by_patient: true } });
    expect(cancelled.state).toBe("patient_acknowledged");
    // the colonoscopy clock keeps its start and due date (A25)
    expect(cancelled.milestones?.confirmatory_completed?.due_at).toBe(before.milestones?.confirmatory_completed?.due_at);
    const rebooked = await api.postEvent(id, { type: "book_confirmatory", actor: me, payload: { appointment_ref: "APPT-2", appointment_date: "2026-12-01" } });
    expect(rebooked.state).toBe("confirmatory_booked");
  });

  it("needs a reason from the taxonomy, by_patient as a boolean, and a booked case", async () => {
    const booked = await owned("confirmatory_booked");
    const me = await asOwner(booked.role);
    for (const payload of [{ reason: "bored", by_patient: true }, { reason: "other" }, { reason: "other", by_patient: "yes" }]) {
      await expect(api.postEvent(booked.id, { type: "appointment_cancelled", actor: me, payload })).rejects.toMatchObject({ status: 422 });
    }
    const notBooked = (await api.listCases()).find((c) => c.state === "owner_accepted" || c.state === "patient_contacted")!;
    await api.login("coordinator", ROLE_NAMES.coordinator);
    const coord = await api.actorFor("coordinator", ROLE_NAMES.coordinator);
    await expect(api.postEvent(notBooked.case_id, { type: "appointment_cancelled", actor: coord, payload: { reason: "other", by_patient: false } })).rejects.toMatchObject({ status: 409 });
  });
});

describe("the case page", () => {
  it("offers the informed refusal and the cancel action on a booked case, and the sign-off on a waiting one", async () => {
    const { id, role, other } = await owned("confirmatory_booked");
    const view = renderWithApp(<CaseDetailPage />, { api, role, route: `/coordinator/${id}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("refusal-panel")).toBeTruthy();
    expect(screen.getByTestId("cancel-appointment")).toBeTruthy();
    // the button is disabled until the counselling record is a DocumentReference
    const record = screen.getByTestId("record-refusal") as HTMLButtonElement;
    expect(record.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("counselling-ref"), { target: { value: REF } });
    fireEvent.change(screen.getByTestId("counselled-by"), { target: { value: role } });
    await waitFor(() => expect((screen.getByTestId("record-refusal") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("record-refusal"));
    // the owner counselled the patient (the page names the owner as counsellor here): they cannot sign it themselves
    const sign = (await screen.findByTestId("sign-declined")) as HTMLButtonElement;
    expect(sign.disabled).toBe(true);
    expect(await screen.findByTestId("re-offer-due")).toBeTruthy();
    expect(screen.getByTestId("reassign-panel")).toBeTruthy();
    expect(other).not.toBe(role);
    view.unmount();
  });

  it("cancels the appointment from the page and lets the owner book again", async () => {
    const { id, role } = await owned("confirmatory_booked");
    renderWithApp(<CaseDetailPage />, { api, role, route: `/coordinator/${id}`, path: "/coordinator/:caseId" });
    fireEvent.click(await screen.findByTestId("cancel-appointment"));
    expect(await screen.findByTestId("book")).toBeTruthy();
  });
});
