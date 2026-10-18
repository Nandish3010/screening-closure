/**
 * A24 (ADR-036) in the web: `review_requested {reason: incomplete_procedure, evidence_ref}` from `system:automation` is the
 * flag that the colonoscopy record in HAPI is not closure evidence. The read model has `incomplete_procedure`; the case page
 * shows a banner; a closure proposal, `close` or `reopen` clears it. The mock mirrors the API (only the automation may give it).
 */
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AUTOMATION_ACTOR, type CaseDetail } from "../src/api/contract";
import { completeDetail, incompleteProcedureOf, reviewReasonOf } from "../src/lib/derive";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { createMockAdapter } from "../src/api/mockAdapter";
import { renderWithApp } from "./helpers";

const ev = (seq: number, type: string, actor: string, payload: Record<string, unknown> = {}) =>
  ({ seq, type, at: `2026-11-0${seq}T09:00:00.000Z`, actor, payload });
const base = [ev(1, "case_created", "system:intake")];
const flag = ev(2, "review_requested", AUTOMATION_ACTOR, { reason: "incomplete_procedure", evidence_ref: "Procedure/colonoscopy-1", source_event_id: "x" });

describe("incomplete_procedure replay", () => {
  it("is set by the automation's review_requested and shown as the review reason", () => {
    expect(incompleteProcedureOf([...base, flag])).toEqual({ evidence_ref: "Procedure/colonoscopy-1", flagged_at: flag.at });
    expect(reviewReasonOf([...base, flag])).toBe("incomplete_procedure");
  });
  it("is cleared by a closure proposal, a close or a reopen", () => {
    for (const clearing of ["closure_proposed", "close", "reopen"]) {
      expect(incompleteProcedureOf([...base, flag, ev(3, clearing, AUTOMATION_ACTOR, { evidence_ref: "Procedure/colonoscopy-1" })])).toBeNull();
    }
  });
  it("passes a server's read_model.incomplete_procedure through unchanged", () => {
    const d = completeDetail({ case_id: "c1", state: "confirmatory_booked", events: [], read_model: { incomplete_procedure: { evidence_ref: "Procedure/p9", flagged_at: "2026-11-03T09:00:00Z" } } } as never);
    expect(d.incomplete_procedure).toEqual({ evidence_ref: "Procedure/p9", flagged_at: "2026-11-03T09:00:00Z" });
  });
});

describe("incomplete_procedure on the case page", () => {
  it("shows an amber banner with the record while the flag is open", async () => {
    const api = createMockAdapter(null, { automation: true });
    const list = await api.listCases();
    const id = list.find((c) => c.state === "confirmatory_booked")?.case_id;
    expect(id).toBeTruthy();
    const real = await api.getCase(id!);
    const flagged: CaseDetail = { ...real, incomplete_procedure: { evidence_ref: "Procedure/colonoscopy-7", flagged_at: "2026-11-03T09:00:00Z" } };
    const stub = { ...api, getCase: async () => flagged } as typeof api;
    renderWithApp(<CaseDetailPage />, { api: stub, role: "coordinator", route: `/coordinator/${id}`, path: "/coordinator/:caseId" });
    const banner = await screen.findByTestId("incomplete-procedure-banner");
    expect(banner.textContent).toMatch(/colonoscopy record is incomplete/i);
    expect(banner.textContent).toMatch(/Procedure\/colonoscopy-7/);
  });
});
