/**
 * D13 (reply triage) in the web: the backend now has `reply_received` / `reply_classified` events from `system:triage`, the read
 * model's `triage_review_reason`, `outreach_holds` and six `review_requested` reasons. There is no triage UI; the web tolerates and
 * displays them, and the mock mirrors the backend's rules (who clears which hold, who may post `contact_updated`).
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { REVIEW_REASONS, type ApiClient, type CaseDetail, type TimelineItem } from "../src/api/contract";
import { DEMO_OPT_OUT_CASE_ID, DEMO_WRONG_NUMBER_CASE_ID } from "../src/api/fixtures";
import { ROLE_PROVIDERS } from "../src/api/identities";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createMockAdapter } from "../src/api/mockAdapter";
import { Timeline } from "../src/components/Timeline";
import { STAFF } from "../src/i18n/strings";
import { completeDetail, outreachHoldsOf, reviewReasonOf, triageReplayOf } from "../src/lib/derive";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { renderWithApp } from "./helpers";

const LAYLA = "primary_care_owner:Dr. Layla Haddad";
const HUDA = "primary_care_owner:Dr. Huda Rahman";
const COORD = "coordinator:Maryam Al Suwaidi";
const TRIAGE = "system:triage";
const auto = () => createMockAdapter(null, { automation: true });
const types = (evs: { type: string }[]) => evs.map((e) => e.type);
const ev = (seq: number, type: string, actor: string, payload: Record<string, unknown> = {}) =>
  ({ seq, type, at: `2026-11-0${seq}T09:00:00.000Z`, actor, payload });

const openCase = (api: ApiClient, id: string) =>
  renderWithApp(<CaseDetailPage />, { api, role: "coordinator", route: `/coordinator/${id}`, path: "/coordinator/:caseId" });

describe("review reasons: the contract and the labels", () => {
  it("lists exactly the seven reasons the backend accepts", () => {
    expect([...REVIEW_REASONS]).toEqual(["consent_withdrawn", "reroute_exhausted", "opt_out", "wrong_number", "triage_uncertain", "already_done", "incomplete_procedure"]);
  });
  it("has an English label for every reason (the staff UI is English; no unreviewed Arabic is added)", () => {
    for (const r of REVIEW_REASONS) {
      expect((STAFF.reviewReasons as Record<string, string>)[r], `en ${r}`).toMatch(/\S/);
    }
    expect(Object.keys(STAFF.reviewReasons).sort()).toEqual([...REVIEW_REASONS].sort());
  });
});

describe("replay of the triage flag and the outreach holds", () => {
  const base = [ev(1, "case_created", "system:intake")];
  it("an opt_out from the triage opens a hold and the triage flag; a person's consent_updated clears both", () => {
    const flagged = [...base, ev(2, "review_requested", TRIAGE, { reason: "opt_out", source_event_id: "x" })];
    expect(triageReplayOf(flagged)).toMatchObject({ holds: ["opt_out"], reason: "opt_out" });
    expect(reviewReasonOf(flagged)).toBe("opt_out");
    const cleared = [...flagged, ev(3, "consent_updated", COORD, { channel_sms: true })];
    expect(triageReplayOf(cleared)).toMatchObject({ holds: [], reason: null });
    expect(reviewReasonOf(cleared)).toBeNull();
  });
  it("a wrong_number hold is cleared by contact_updated, not by consent_updated", () => {
    const flagged = [...base, ev(2, "review_requested", TRIAGE, { reason: "wrong_number", source_event_id: "x" })];
    const afterConsent = [...flagged, ev(3, "consent_updated", COORD, { channel_sms: true })];
    expect(outreachHoldsOf(afterConsent)).toEqual(["wrong_number"]);
    const afterContact = [...flagged, ev(3, "contact_updated", COORD, { contact_valid: true })];
    expect(triageReplayOf(afterContact)).toMatchObject({ holds: [], reason: null, contactConfirmedAt: afterContact[2].at });
  });
  it("triage_uncertain and already_done are flags, not holds", () => {
    for (const reason of ["triage_uncertain", "already_done"]) {
      const flagged = [...base, ev(2, "review_requested", TRIAGE, { reason, source_event_id: "x" })];
      expect(outreachHoldsOf(flagged)).toEqual([]);
      expect(reviewReasonOf(flagged)).toBe(reason);
    }
  });
  it("consent_withdrawn comes first, then the triage reason, then reroute_exhausted", () => {
    const flagged = [...base, ev(2, "review_requested", TRIAGE, { reason: "opt_out", source_event_id: "x" })];
    const withdrawn = [...flagged, ev(3, "consent_updated", "patient:p", { withdrawn: true }), ev(4, "review_requested", "system:consent", { reason: "consent_withdrawn", source_event_id: "y" })];
    expect(reviewReasonOf(withdrawn)).toBe("consent_withdrawn");
    // The person's answer was to withdraw: the hold is gone but the triage reason stays for the consent reviewer.
    expect(triageReplayOf(withdrawn)).toMatchObject({ holds: [], reason: "opt_out" });
    const restored = [...withdrawn, ev(5, "consent_updated", COORD, { withdrawn: false })];
    expect(reviewReasonOf(restored)).toBe("opt_out");
  });
  it("completeDetail fills the new read-model fields from the log, and keeps the server's when it sends them", () => {
    const events = [...base, ev(2, "review_requested", TRIAGE, { reason: "wrong_number", source_event_id: "x" })];
    const d = completeDetail({ case_id: "c", events } as unknown as CaseDetail);
    expect(d).toMatchObject({ review_reason: "wrong_number", triage_review_reason: "wrong_number", outreach_holds: ["wrong_number"], review_requested_at: events[1].at });
    const wire = completeDetail({ case_id: "c", events, read_model: { review_reason: "consent_withdrawn", triage_review_reason: "wrong_number", outreach_holds: ["wrong_number"] } } as unknown as CaseDetail);
    expect(wire).toMatchObject({ review_reason: "consent_withdrawn", triage_review_reason: "wrong_number", outreach_holds: ["wrong_number"] });
  });
});

describe("timeline: the reply events", () => {
  const items = [
    { kind: "event", seq: 1, at: "2026-11-01T09:00:00Z", type: "reply_received", actor: TRIAGE, actor_kind: "service", payload: { channel: "sms", text_ref: "7d1b2f0e-5c4a-4e8b-9a3d-0f6e1c2b3a41" } },
    { kind: "event", seq: 2, at: "2026-11-01T09:00:01Z", type: "reply_classified", actor: TRIAGE, actor_kind: "service", payload: { intent: "wrong_number", confidence: 0.91, model_id: "triage-rules-1", needs_human: true, text_ref: "7d1b2f0e-5c4a-4e8b-9a3d-0f6e1c2b3a41" } },
    { kind: "event", seq: 3, at: "2026-11-01T09:00:02Z", type: "review_requested", actor: TRIAGE, actor_kind: "service", payload: { reason: "triage_uncertain", source_event_id: "x" } },
    { kind: "event", seq: 4, at: "2026-11-01T09:00:03Z", type: "review_requested", actor: TRIAGE, actor_kind: "service", payload: { reason: "already_done", source_event_id: "x" } },
    { kind: "event", seq: 5, at: "2026-11-01T09:00:04Z", type: "contact_updated", actor: COORD, actor_kind: "human", payload: { contact_valid: true } },
  ] as TimelineItem[];
  const row = (type: string, n = 0) => within(screen.getByTestId("timeline")).getAllByText((_, el) => el?.tagName === "LI" && el.getAttribute("data-event-type") === type)[n];

  it("labels the two reply events and the new flag reasons, shows the channel and the classification, and never a reply text", () => {
    render(<Timeline items={items} />);
    expect(within(row("reply_received")).getByText(STAFF.replyReceived)).toBeTruthy();
    expect(row("reply_received").textContent).toContain("channel: sms");
    expect(row("reply_received").textContent).not.toContain("7d1b2f0e");
    expect(within(row("reply_classified")).getByText(STAFF.replyClassified)).toBeTruthy();
    expect(row("reply_classified").textContent).toContain("intent: wrong number");
    expect(row("reply_classified").textContent).toContain("91%");
    expect(row("reply_classified").textContent).toContain(STAFF.replyClassifiedNeedsHuman);
    expect(within(row("review_requested", 0)).getByText(STAFF.reviewReasons.triage_uncertain)).toBeTruthy();
    expect(within(row("review_requested", 1)).getByText(STAFF.reviewReasons.already_done)).toBeTruthy();
    expect(within(row("contact_updated")).getByText(STAFF.contactUpdated)).toBeTruthy();
  });
  it("marks the triage as a service, not as a person and not as the automation", () => {
    render(<Timeline items={items} />);
    expect(row("reply_received").getAttribute("data-actor-kind")).toBe("service");
    expect(within(row("reply_received")).queryByTestId("automation-chip")).toBeNull();
    expect(within(row("reply_received")).getByTestId("automated-chip")).toBeTruthy();
  });
});

describe("mock adapter: the seeded triage cases", () => {
  it("an opt-out case: flag, hold, reply events written by the triage, and no text in any event", async () => {
    const api = auto();
    const d = await api.getCase(DEMO_OPT_OUT_CASE_ID);
    expect(d).toMatchObject({ review_reason: "opt_out", triage_review_reason: "opt_out", outreach_holds: ["opt_out"] });
    expect(types(d.events)).toEqual(expect.arrayContaining(["reply_received", "reply_classified", "review_requested"]));
    for (const e of d.events.filter((x) => x.actor === TRIAGE)) {
      expect(e.actor_kind).toBe("service");
      expect(Object.keys(e.payload)).not.toContain("text");
    }
  });
  it("a person's consent_updated clears the opt_out hold and the flag, and the automation stays quiet afterwards", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    const d = await api.postEvent(DEMO_OPT_OUT_CASE_ID, { type: "consent_updated", actor: COORD, payload: { channel_sms: true } });
    expect(d).toMatchObject({ review_reason: null, triage_review_reason: null, outreach_holds: [] });
  });
  it("a consent_updated that withdraws consent clears the hold but the case stays in consent review with the triage reason kept", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    const d = await api.postEvent(DEMO_OPT_OUT_CASE_ID, { type: "consent_updated", actor: COORD, payload: { withdrawn: true } });
    expect(d).toMatchObject({ review_reason: "consent_withdrawn", triage_review_reason: "opt_out", outreach_holds: [] });
  });
  it("a wrong_number hold is not cleared by consent, but by contact_updated {contact_valid: true}", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    let d = await api.postEvent(DEMO_WRONG_NUMBER_CASE_ID, { type: "consent_updated", actor: COORD, payload: { channel_sms: true } });
    expect(d.outreach_holds).toEqual(["wrong_number"]);
    d = await api.postEvent(DEMO_WRONG_NUMBER_CASE_ID, { type: "contact_updated", actor: COORD, payload: { contact_valid: true } });
    expect(d).toMatchObject({ review_reason: null, triage_review_reason: null, outreach_holds: [] });
    expect(d.events.at(-1)).toMatchObject({ type: "contact_updated", actor: COORD, actor_kind: "human" });
  });
});

describe("mock adapter: contact_updated and the triage events are guarded like the backend", () => {
  it("accepts contact_updated from the coordinator, the supervisor and the case's owners; refuses the lab and the patient", async () => {
    const api = auto();
    await api.login("supervisor", "Dr. Noura Al Hammadi");
    expect((await api.postEvent(DEMO_WRONG_NUMBER_CASE_ID, { type: "contact_updated", actor: "supervisor:Dr. Noura Al Hammadi", payload: { contact_valid: true } })).outreach_holds).toEqual([]);
    // The owner of case 21 is Dr. Huda Rahman (a primary_care_owner login).
    await api.login("clinician", "Dr. Huda Rahman", ROLE_PROVIDERS.clinician);
    expect((await api.postEvent(DEMO_WRONG_NUMBER_CASE_ID, { type: "contact_updated", actor: HUDA, payload: { contact_valid: true } })).events.at(-1)!.type).toBe("contact_updated");
    await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
    expect((await api.postEvent(DEMO_OPT_OUT_CASE_ID, { type: "contact_updated", actor: LAYLA, payload: { contact_valid: true } })).events.at(-1)!.type).toBe("contact_updated");
    await api.login("patient", "Patient");
    await expect(api.postEvent(DEMO_OPT_OUT_CASE_ID, { type: "contact_updated", actor: "patient:c0000020", payload: { contact_valid: true } })).rejects.toMatchObject({ status: 403 });
    await api.login("coordinator", "Maryam Al Suwaidi");
    await expect(api.postEvent(DEMO_OPT_OUT_CASE_ID, { type: "contact_updated", actor: "lab:Central Lab", payload: { contact_valid: true } })).rejects.toMatchObject({ status: 403 });
  });
  it("an owner may not confirm contact details on a case they do not own", async () => {
    const api = auto();
    await api.login("clinician", "Dr. Huda Rahman", ROLE_PROVIDERS.clinician);
    await expect(api.postEvent(DEMO_OPT_OUT_CASE_ID, { type: "contact_updated", actor: HUDA, payload: { contact_valid: true } })).rejects.toMatchObject({ status: 403 });
  });
  it("contact_updated needs contact_valid: true", async () => {
    const api = auto();
    await api.login("coordinator", "Maryam Al Suwaidi");
    for (const payload of [{}, { contact_valid: false }, { contact_valid: "yes" }]) {
      await expect(api.postEvent(DEMO_WRONG_NUMBER_CASE_ID, { type: "contact_updated", actor: COORD, payload })).rejects.toMatchObject({ status: 422 });
    }
  });
  it("nobody posts reply_received, reply_classified or a triage review_requested through the events route", async () => {
    const api = auto();
    for (const role of ["coordinator", "supervisor"] as const) {
      await api.login(role, "X");
      for (const [type, payload] of [
        ["reply_received", { channel: "sms", text_ref: "t" }],
        ["reply_classified", { intent: "other", confidence: 0.5, model_id: "m", needs_human: true, text_ref: "t" }],
        ["review_requested", { reason: "opt_out", source_event_id: "x" }],
      ] as const) {
        await expect(api.postEvent(DEMO_WRONG_NUMBER_CASE_ID, { type, actor: TRIAGE, payload: { ...payload } })).rejects.toMatchObject({ status: 403 });
      }
    }
  });
  it("a patient cannot see the reply or triage events in their own view", async () => {
    const api = auto();
    await api.login("patient", "Patient");
    const d = await api.getCase(DEMO_OPT_OUT_CASE_ID);
    expect(types(d.events)).not.toEqual(expect.arrayContaining(["reply_received"]));
    expect(types(d.events)).not.toEqual(expect.arrayContaining(["reply_classified"]));
    expect(types(d.events)).not.toEqual(expect.arrayContaining(["review_requested"]));
  });
});

describe("mock automation: no automatic message while a hold is open", () => {
  /** A fresh case, then the triage's wrong-number flag written straight into the stored log (the mock has no inbound replies). */
  async function caseWithHold(hold: "wrong_number" | "opt_out") {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
    const first = createMockAdapter(storage, { automation: true });
    await first.login("coordinator", "Maryam Al Suwaidi");
    const id = await first.createCase!();
    const key = [...store.keys()].find((k) => k.startsWith("sc.mock.v"))!;
    const db = JSON.parse(store.get(key)!);
    const at = new Date(Date.parse(db.events[id].at(-1).at) + 1000).toISOString();
    db.events[id].push({ seq: db.events[id].length + 1, type: "review_requested", at, actor: TRIAGE, payload: { reason: hold, source_event_id: "x" } });
    store.set(key, JSON.stringify(db));
    return { api: createMockAdapter(storage, { automation: true }), id };
  }
  for (const hold of ["wrong_number", "opt_out"] as const) {
    it(`accepting ownership sends no template message while ${hold} is open`, async () => {
      const { api, id } = await caseWithHold(hold);
      await api.login("owner", "Dr. Layla Haddad", ROLE_PROVIDERS.owner);
      const d = await api.postEvent(id, { type: "accept_ownership", actor: LAYLA, payload: {} });
      expect(d.state).toBe("owner_accepted");
      expect(types(d.events)).not.toContain("message_sent");
      expect(d.outreach_holds).toEqual([hold]);
    });
  }
});

describe("case page: the flag reason", () => {
  it("shows the triage reason and the held messages on a case flagged by the triage", async () => {
    openCase(auto(), DEMO_OPT_OUT_CASE_ID);
    const banner = await screen.findByTestId("triage-review-banner");
    expect(banner.getAttribute("data-reason")).toBe("opt_out");
    expect(banner.textContent).toContain(STAFF.reviewReasons.opt_out);
    expect(banner.textContent).toContain(STAFF.holdsLabel);
    await waitFor(() => expect(screen.getByTestId("timeline").textContent).toContain(STAFF.replyReceived));
  });
  it("shows no triage banner on a case without a triage flag", async () => {
    openCase(auto(), "c0000019-0000-4000-8000-000000000019");
    await screen.findByTestId("timeline");
    expect(screen.queryByTestId("triage-review-banner")).toBeNull();
  });
});

describe("http adapter: the read model of a triage-flagged case", () => {
  it("passes triage_review_reason, outreach_holds and the consent-first review_reason through from the nested read_model", async () => {
    const events = [{ seq: 1, type: "case_created", at: "2026-10-01T00:00:00Z", actor: "system:intake", payload: {} }];
    const body = {
      case_id: "c1", state: "owner_accepted", events,
      read_model: {
        state: "owner_accepted", review_reason: "wrong_number", review_requested_at: "2026-10-02T00:00:00Z",
        triage_review_reason: "wrong_number", triage_review_requested_at: "2026-10-02T00:00:00Z", outreach_holds: ["wrong_number"],
      },
    };
    const fn = (async () => new Response(JSON.stringify(body), { status: 200 })) as unknown as typeof fetch;
    const d = await createHttpAdapter("http://api.test", fn).getCase("c1");
    expect(d).toMatchObject({ review_reason: "wrong_number", triage_review_reason: "wrong_number", outreach_holds: ["wrong_number"], review_requested_at: "2026-10-02T00:00:00Z" });
  });
});
