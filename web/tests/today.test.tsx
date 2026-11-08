/**
 * T-C22 (C14): the coordinator's Today screen. Seven queues of what needs a person now; each item shows the patient's short id,
 * pathway, priority and a plain-words reason; one-click actions use the existing events and respect the role; every queue has a
 * count and an "all clear" state; a scan that is not complete never says all clear.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AppProvider } from "../src/app/AppContext";
import type { ApiClient, Role, TodayGroup, TodayGroupId, TodayItem, TodayResponse } from "../src/api/contract";
import { TODAY_CASE_GROUPS, TODAY_GROUPS } from "../src/api/contract";
import { DEMO_OPT_OUT_CASE_ID, DEMO_WRONG_NUMBER_CASE_ID, DEMO_CLOSURE_PROPOSED_CASE_ID, DEMO_OVERDUE_CASE_ID } from "../src/api/fixtures";
import { createHttpAdapter } from "../src/api/httpAdapter";
import { createMockAdapter } from "../src/api/mockAdapter";
import App from "../src/App";
import { TODAY } from "../src/i18n/strings";
import { shortId } from "../src/lib/format";
import { actionState, mergeGroups, replyNotes, whyText } from "../src/lib/today";
import { TodayPage } from "../src/pages/TodayPage";
import { renderWithApp } from "./helpers";

const ids = (r: TodayResponse, g: TodayGroupId) => r.groups.find((x) => x.group === g)!.items.map((i) => ("case_id" in i ? i.case_id : ""));
const item = (over: Partial<TodayItem> = {}): TodayItem => ({
  case_id: "11111111-2222-4333-8444-555555555555", pathway: "colorectal_fit", state: "acknowledged", priority: null, owner: null, provider: null,
  due_at: null, last_event_at: "2026-11-01T00:00:00Z", since: "2026-11-01T00:00:00Z", why: { code: "overdue", days: 3 }, actions: ["open_case"], ...over,
});
const group = (g: TodayGroupId, items: TodayItem[], more = false): TodayGroup => ({ group: g, items, count: items.length, more });
const response = (groups: TodayGroup[], over: Partial<TodayResponse> = {}): TodayResponse => ({
  sim_time: "2026-11-04T09:00:00Z", limit: 10, scanned: 3, cursor: null, complete: true, agency_cursor: null, groups, ...over,
});
const empty = () => TODAY_GROUPS.map((g) => group(g, []));
const withToday = (api: ApiClient, getToday: ApiClient["getToday"]): ApiClient => Object.assign(Object.create(api), api, { getToday });
const page = (api: ApiClient, role: Role = "coordinator") => renderWithApp(<TodayPage />, { api, role, route: "/coordinator/today" });

describe("the mock's queues are the read model's", () => {
  it("lists the seven queues in order, and every case queue holds exactly what the worklist facts say", async () => {
    const api = createMockAdapter(null, { automation: true });
    const r = await api.getToday({ limit: 50 });
    expect(r.groups.map((g) => g.group)).toEqual([...TODAY_GROUPS]);
    expect(r.complete).toBe(true);
    expect(r.cursor).toBeNull();
    const open = (await api.listCases()).filter((c) => c.state !== "closed");
    expect(new Set(ids(r, "overdue"))).toEqual(new Set(open.filter((c) => c.overdue).map((c) => c.case_id)));
    expect(new Set(ids(r, "flagged"))).toEqual(new Set(open.filter((c) => c.watchdog_flagged).map((c) => c.case_id)));
    expect(ids(r, "overdue")).toContain(DEMO_OVERDUE_CASE_ID);
    expect(ids(r, "replies")).toEqual(expect.arrayContaining([DEMO_OPT_OUT_CASE_ID, DEMO_WRONG_NUMBER_CASE_ID]));
    expect(ids(r, "signatures")).toContain(DEMO_CLOSURE_PROPOSED_CASE_ID);
    for (const g of TODAY_CASE_GROUPS) {
      for (const it of r.groups.find((x) => x.group === g)!.items as TodayItem[]) {
        expect(open.map((c) => c.case_id)).toContain(it.case_id); // a closed case is in no queue
        expect(it.why.code).toBeTruthy();
        expect(it.actions).toContain("open_case");
      }
    }
  });

  it("is for the coordinator and the supervisor only", async () => {
    const api = createMockAdapter(null);
    for (const role of ["owner", "specialist", "clinician", "patient"] as Role[]) {
      await api.login(role, "x");
      await expect(api.getToday()).rejects.toMatchObject({ status: 403 });
    }
    await api.login("supervisor", "x");
    await expect(api.getToday()).resolves.toBeTruthy();
  });
});

describe("words and rules", () => {
  it("says why in plain words, never a code", () => {
    expect(whyText(item({ why: { code: "overdue", days: 3 } }))).toBe("It is 3 days past its deadline.");
    expect(whyText(item({ why: { code: "urgent_untouched", days: 0 } }))).toBe("An urgent result came in today and nobody has acted on it yet.");
    expect(whyText(item({ why: { code: "escalation_waiting", rung: "supervisor" } }))).toMatch(/escalated to owner's supervisor and a person has to accept it/);
    expect(whyText(item({ why: { code: "reply_waiting", reason: "triage_uncertain" } }))).toMatch(/could not be read with confidence/);
    expect(whyText(item({ why: { code: "callback_waiting" } }))).toMatch(/call-back/);
    expect(whyText(item({ why: { code: "nobody_looking", idle_days: 11 } }))).toBe("Nobody has looked at this case for 11 days.");
    expect(whyText(item({ why: { code: "closure_proposed" } }))).toMatch(/physician/);
    expect(whyText(item({ why: { code: "declined_signoff" } }))).toMatch(/did not counsel/);
  });

  it("notes the Gulf-dialect rule and a low model confidence, never the text", () => {
    const notes = replyNotes(item({ reply: { reason: "triage_uncertain", callback: false, holds: [], intent: "reschedule", confidence: 0.41, forced_reason: "ar_gulf" } }));
    expect(notes).toEqual([TODAY.reasons.ar_gulf, 'Model confidence 0.41 (read as "Reschedule")']);
  });

  it("allows an action only to the roles the API allows", () => {
    const esc = item({ escalation: { rung: "supervisor", level: 2, target: "supervisor", accepted_by: ["supervisor"], milestone: null, at: null } });
    expect(actionState("accept_escalation", "supervisor", esc).allowed).toBe(true);
    const denied = actionState("accept_escalation", "coordinator", esc);
    expect(denied).toMatchObject({ allowed: false });
    expect(denied.why).toBe("This rung is for a supervisor to accept. You track the case: open it to see what is waiting.");
    expect(actionState("take_ownership", "coordinator", item()).allowed).toBe(false);
    expect(actionState("take_ownership", "supervisor", item()).allowed).toBe(true);
    expect(actionState("take_ownership", "owner", item()).allowed).toBe(true);
    expect(actionState("confirm_contact", "coordinator", item()).allowed).toBe(true);
    expect(actionState("open_case", "coordinator", item()).allowed).toBe(true);
  });

  it("merges a page without repeating an item, longest-waiting first", () => {
    const a = item({ case_id: "a", since: "2026-11-03T00:00:00Z" });
    const b = item({ case_id: "b", since: "2026-11-01T00:00:00Z" });
    const merged = mergeGroups({ overdue: [a] }, [group("overdue", [a, b])]);
    expect(merged.overdue.map((i) => ("case_id" in i ? i.case_id : ""))).toEqual(["b", "a"]);
  });
});

describe("the screen", () => {
  it("shows the heading, the synthetic-data label, the full worklist link and a count for every queue", async () => {
    page(createMockAdapter(null, { automation: true }));
    expect(await screen.findByRole("heading", { level: 1, name: "Today" })).toBeTruthy();
    expect(screen.getByText("Synthetic data")).toBeTruthy();
    expect(screen.getByTestId("full-worklist").getAttribute("href")).toBe("/coordinator");
    expect(await screen.findByTestId("today-list")).toBeTruthy();
    expect(screen.getByTestId("today-summary").textContent).toMatch(/^\d+\+? .*One list, most urgent first/);
    expect(screen.getByTestId("today-filter-all")).toBeTruthy();
    for (const g of TODAY_GROUPS) {
      const n = screen.queryByTestId(`today-count-${g}`);
      if (n) expect(n.textContent).toMatch(/^\d+\+?$/); // a filter chip exists only for a queue that has something in it
    }
    expect(screen.queryAllByText(/All clear/)).toHaveLength(0); // no stack of empty sections
    expect(screen.getByTestId("today-scan").textContent).toMatch(/^All \d+ open cases read\./);
  });

  it("each item carries the short id, pathway, priority and the reason in plain words", async () => {
    const api = withToday(createMockAdapter(null), async () => response([
      ...empty().filter((g) => g.group !== "urgent"),
      group("urgent", [item({ priority: { tier: "urgent", rank: 0, rule: "bp_very_high", reason: { en: "Very high blood pressure", ar: "" }, basis: "PLACEHOLDER, needs clinician sign-off (H1)" },
        pathway: "chronic_conditions", why: { code: "urgent_untouched", days: 2 } })]),
    ]));
    page(api);
    const row = await screen.findByTestId("today-item");
    expect(within(row).getByTestId("today-case-id").textContent).toBe("11111111");
    expect(within(row).getByTestId("today-case-id").getAttribute("href")).toBe("/coordinator/11111111-2222-4333-8444-555555555555");
    expect(within(row).getByTestId("today-pathway").getAttribute("data-pathway")).toBe("chronic_conditions");
    expect(within(row).getByTestId("today-priority").getAttribute("data-priority")).toBe("urgent");
    expect(within(row).getByTestId("today-why").textContent).toBe("An urgent result came in 2 days ago and nobody has acted on it yet.");
    expect(within(row).getByTestId("today-priority-text").textContent).toBe("Very high blood pressure.");
    expect(within(row).getByTestId("today-priority-placeholder").textContent).toMatch(/placeholder pending clinician sign-off$/);
    expect(within(row).getByTestId("today-open-case")).toBeTruthy();
    expect(screen.getByTestId("today-count-urgent").textContent).toBe("1");
  });

  it("a case whose priority is not a placeholder shows its reason and no placeholder flag, and a case without priority shows neither", async () => {
    const api = withToday(createMockAdapter(null), async () => response([
      ...empty().filter((g) => g.group !== "urgent"),
      group("urgent", [
        item({ case_id: "aaaaaaaa-2222-4333-8444-555555555555", priority: { tier: "urgent", rank: 0, rule: "x", reason: { en: "A sourced rule", ar: "" }, basis: "IFHAS V10" } }),
        item({ case_id: "bbbbbbbb-2222-4333-8444-555555555555", priority: null }),
      ]),
    ]));
    page(api);
    const rows = await screen.findAllByTestId("today-item");
    expect(within(rows[0]).getByTestId("today-priority-text").textContent).toBe("A sourced rule.");
    expect(within(rows[0]).queryByTestId("today-priority-placeholder")).toBeNull();
    expect(within(rows[1]).queryByTestId("today-priority-reason")).toBeNull();
  });

  it("when nothing is waiting there is one empty state, not one per queue", async () => {
    page(withToday(createMockAdapter(null), async () => response(empty())));
    expect((await screen.findByTestId("today-empty")).textContent).toContain("Nobody is waiting on you");
    expect(screen.getAllByRole("status").filter((e) => /All clear/.test(e.textContent ?? ""))).toHaveLength(0);
    expect(screen.getByTestId("today-summary").textContent).toContain("Nobody is waiting on you.");
  });

  it("filter chips narrow the one list and say which is on", async () => {
    page(createMockAdapter(null, { automation: true }));
    await screen.findAllByTestId("today-item");
    const all = screen.getAllByTestId("today-item").length;
    fireEvent.click(screen.getByTestId("today-filter-overdue"));
    expect(screen.getByTestId("today-filter-overdue").getAttribute("aria-pressed")).toBe("true");
    const rows = screen.getAllByTestId("today-item");
    expect(rows.length).toBeLessThan(all);
    for (const r of rows) expect(r.getAttribute("data-group")).toBe("overdue");
    fireEvent.click(screen.getByTestId("today-filter-all"));
    expect(screen.getAllByTestId("today-item")).toHaveLength(all);
  });

  it("never says all clear while the scan is incomplete; 'Look further' reads on with the cursor", async () => {
    const calls: unknown[] = [];
    const api = withToday(createMockAdapter(null), async (p) => {
      calls.push(p);
      return p?.cursor
        ? response([group("overdue", [item({ case_id: "late" })])], { cursor: null, complete: true, scanned: 4 })
        : response(empty(), { cursor: "c1", complete: false, scanned: 1000 });
    });
    page(api);
    expect(await screen.findByTestId("today-searching")).toBeTruthy();
    expect(screen.queryByTestId("today-empty")).toBeNull();
    expect(screen.getByTestId("today-scan").textContent).toContain("1000 open cases read so far");
    fireEvent.click(screen.getByTestId("today-more"));
    await waitFor(() => expect(screen.getByTestId("today-count-overdue").textContent).toBe("1"));
    expect(calls.find((p) => (p as { cursor?: string })?.cursor)).toMatchObject({ cursor: "c1", groups: [...TODAY_CASE_GROUPS] });
    expect(screen.queryByTestId("today-searching")).toBeNull(); // complete now
  });

  it("is not for a clinician: it says so and asks the API for nothing", async () => {
    const getToday = vi.fn();
    page(withToday(createMockAdapter(null), getToday), "owner");
    expect((await screen.findByTestId("today-not-for-role")).textContent).toContain("care coordinator and the supervisor");
    expect(getToday).not.toHaveBeenCalled();
  });

  it("shows an API failure as an alert", async () => {
    page(withToday(createMockAdapter(null), async () => { throw new Error("boom"); }));
    expect((await screen.findByTestId("today-error")).textContent).toContain("boom");
  });
});

describe("one-click actions", () => {
  const escItem = item({
    why: { code: "escalation_waiting", rung: "coordinator", level: 3 }, actions: ["accept_escalation", "open_case"],
    escalation: { rung: "coordinator", level: 3, target: "coordinator", accepted_by: ["coordinator"], milestone: null, at: null },
  });

  it("accepts an escalation with the same event the case page posts, reloads, and moves focus to the queue's heading", async () => {
    const base = createMockAdapter(null);
    const posted: unknown[] = [];
    let accepted = false;
    const api = Object.assign(Object.create(base), base, {
      getToday: async () => response([...empty().filter((g) => g.group !== "escalations"), group("escalations", accepted ? [] : [escItem])]),
      postEvent: async (id: string, body: unknown) => { posted.push([id, body]); accepted = true; return {}; },
    }) as ApiClient;
    page(api);
    const btn = await screen.findByTestId("today-accept_escalation");
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    await act(async () => { fireEvent.click(btn); });
    await waitFor(() => expect(screen.getByTestId("today-flash").textContent).toContain("Escalation accepted."));
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject([escItem.case_id, { type: "escalation_accepted", payload: { rung: "coordinator" } }]);
    expect((posted[0] as [string, { actor: string }])[1].actor).toMatch(/^coordinator:/);
    expect(screen.getByTestId("today-empty")).toBeTruthy();
    expect(document.activeElement?.id).toBe("today-empty"); // the list is gone, so focus lands on the empty state
  });

  it("a role the rung is not for sees the action disabled, with the reason in words", async () => {
    const supOnly = { ...escItem, escalation: { ...escItem.escalation!, rung: "supervisor", accepted_by: ["supervisor"] } };
    page(withToday(createMockAdapter(null), async () => response([...empty().filter((g) => g.group !== "escalations"), group("escalations", [supOnly])])), "coordinator");
    const btn = (await screen.findByTestId("today-accept_escalation")) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId("today-accept_escalation-why").textContent).toBe("This rung is for a supervisor to accept. You track the case: open it to see what is waiting.");
    expect(btn.getAttribute("aria-describedby")).toBe(screen.getByTestId("today-accept_escalation-why").id);
  });

  it("take ownership is for the supervisor and the owner clinicians; the coordinator is told why not", async () => {
    const flagged = item({ why: { code: "nobody_looking", idle_days: 11 }, actions: ["open_case", "take_ownership"], state: "acknowledged" });
    const mk = () => withToday(createMockAdapter(null), async () => response([...empty().filter((g) => g.group !== "flagged"), group("flagged", [flagged])]));
    const { unmount } = page(mk(), "coordinator");
    expect(((await screen.findByTestId("today-take_ownership")) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("today-take_ownership-why").textContent).toContain("The case-owner physician takes this one; a coordinator tracks and chases");
    unmount();
    page(mk(), "supervisor");
    expect(((await screen.findByTestId("today-take_ownership")) as HTMLButtonElement).disabled).toBe(false);
  });

  it("confirming the contact on a wrong-number reply clears it from the queue (mock, end to end)", async () => {
    const api = createMockAdapter(null, { automation: true });
    page(api);
    const row = (await screen.findAllByTestId("today-item")).find((r) => r.getAttribute("data-case") === DEMO_WRONG_NUMBER_CASE_ID)!;
    expect(row).toBeTruthy();
    expect(within(row).getByTestId("today-why").textContent).toMatch(/number is wrong/);
    await act(async () => { fireEvent.click(within(row).getByTestId("today-confirm_contact")); });
    await waitFor(() => expect(screen.getByTestId("today-flash").textContent).toContain(`(${shortId(DEMO_WRONG_NUMBER_CASE_ID)})`));
    expect(screen.getAllByTestId("today-item").some((r) => r.getAttribute("data-case") === DEMO_WRONG_NUMBER_CASE_ID && r.getAttribute("data-group") === "replies")).toBe(false);
    const events = (await api.getCase(DEMO_WRONG_NUMBER_CASE_ID)).events;
    expect(events[events.length - 1]).toMatchObject({ type: "contact_updated" }); // the audit trail is the case page's own
  });

  it("an engine refusal is shown verbatim and nothing else changes", async () => {
    const base = createMockAdapter(null);
    const api = Object.assign(Object.create(base), base, {
      getToday: async () => response([...empty().filter((g) => g.group !== "escalations"), group("escalations", [escItem])]),
      postEvent: async () => { const { ApiError } = await import("../src/api/contract"); throw new ApiError(409, "rung 'coordinator' is already accepted"); },
    }) as ApiClient;
    page(api);
    const btn = await screen.findByTestId("today-accept_escalation");
    await act(async () => { fireEvent.click(btn); });
    expect((await screen.findByTestId("today-error")).textContent).toContain("The engine refused this: rung 'coordinator' is already accepted");
    expect(screen.getByTestId("today-item")).toBeTruthy();
  });

  it("take ownership: busy label while it runs, and a conflict says someone else got there first", async () => {
    const flagged = item({ why: { code: "nobody_looking", idle_days: 11 }, actions: ["open_case", "take_ownership"], state: "acknowledged" });
    const base = createMockAdapter(null);
    let release: () => void = () => undefined;
    const api = Object.assign(Object.create(base), base, {
      getToday: async () => response([...empty().filter((g) => g.group !== "flagged"), group("flagged", [flagged])]),
      postEvent: () => new Promise((_, rej) => { release = async () => { const { ApiError } = await import("../src/api/contract"); rej(new ApiError(409, "case already has an owner")); }; }),
    }) as ApiClient;
    page(api, "supervisor");
    const btn = await screen.findByTestId("today-take_ownership");
    expect(btn.textContent).toBe("Take ownership");
    await act(async () => { fireEvent.click(btn); });
    expect(screen.getByTestId("today-take_ownership").textContent).toBe("Taking ownership...");
    expect((screen.getByTestId("today-take_ownership") as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { release(); });
    expect((await screen.findByTestId("today-conflict")).textContent).toContain("Someone else took this case first");
    expect(screen.getByTestId("today-take_ownership").textContent).toBe("Take ownership");
  });

  it("every action and link is a native control in the tab order", async () => {
    page(createMockAdapter(null, { automation: true }));
    await screen.findAllByTestId("today-item");
    for (const el of screen.getAllByTestId("today-open-case")) expect(el.tagName).toBe("A");
    for (const b of screen.getAllByRole("button")) expect(b.getAttribute("tabindex")).not.toBe("-1");
    expect(screen.getByTestId("today-list").getAttribute("aria-labelledby")).toBe("today-h-list");
    for (const b of screen.getAllByTestId(/today-filter-/)) expect(b.getAttribute("aria-pressed")).not.toBeNull();
  });
});

describe("landing", () => {
  const app = (role: Role, route = "/") => render(
    <MemoryRouter initialEntries={[route]}>
      <AppProvider api={createMockAdapter(null, { automation: true })} initialRole={role}><App /></AppProvider>
    </MemoryRouter>,
  );
  it("the coordinator lands on Today, with the worklist one click away", async () => {
    app("coordinator");
    expect(await screen.findByRole("heading", { level: 1, name: "Today" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Today" }).getAttribute("href")).toBe("/coordinator/today");
    expect(screen.getByTestId("full-worklist").getAttribute("href")).toBe("/coordinator");
  });
  it("the worklist is still at /coordinator", async () => {
    app("coordinator", "/coordinator");
    expect(await screen.findByRole("heading", { level: 1, name: "Worklist" })).toBeTruthy();
  });
  it("a physician does not get the Today screen: she lands on her own work", async () => {
    app("owner");
    await waitFor(() => expect(screen.queryByRole("heading", { level: 1, name: "Today" })).toBeNull());
    await waitFor(() => expect(document.body.textContent).toMatch(/Your cases|Case\s+c[0-9a-f]+/i));
    expect(screen.queryByRole("link", { name: "Today" })).toBeNull();
  });
});

describe("http adapter", () => {
  it("sends limit, cursors and repeated group params to GET /today", async () => {
    const calls: string[] = [];
    const fn = vi.fn(async (url: string | URL | Request) => {
      calls.push(String(url));
      return new Response(JSON.stringify(response([])), { status: 200 });
    }) as unknown as typeof fetch;
    const api = createHttpAdapter("http://api.test", fn);
    await api.getToday({ limit: 10, cursor: "c1", agencyCursor: "a1", groups: ["overdue", "flagged"] });
    await api.getToday();
    expect(calls).toEqual(["http://api.test/today?limit=10&cursor=c1&agency_cursor=a1&group=overdue&group=flagged", "http://api.test/today"]);
  });
});


describe("Today for the coordinator: the role is told what it does, and has work it can do", () => {
  it("shows the coordinator's own line (you track; the physician decides) and nobody else's", async () => {
    const mk = () => withToday(createMockAdapter(null), async () => response(empty()));
    const { unmount } = page(mk(), "coordinator");
    const note = await screen.findByTestId("today-coordinator-note");
    expect(note).toHaveTextContent(/you track and chase; the physician decides/i);
    expect(note).toHaveTextContent(/phone patients back/i);
    unmount();
    page(mk(), "supervisor");
    await screen.findByTestId("today-summary");
    expect(screen.queryByTestId("today-coordinator-note")).toBeNull();
  });

  it("a patient's call-back request is the coordinator's to answer: the button is enabled for them, with no 'only a supervisor' wall", async () => {
    const cb = item({ why: { code: "callback_waiting", reason: "wants_to_speak_to_clinician" }, actions: ["record_callback", "open_case"], state: "owner_accepted" });
    page(withToday(createMockAdapter(null), async () => response([...empty().filter((g) => g.group !== "replies"), group("replies", [cb])])), "coordinator");
    const btn = (await screen.findByTestId("today-record_callback")) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(screen.queryByTestId("today-record_callback-why")).toBeNull();
    expect(actionState("record_callback", "coordinator", cb).allowed).toBe(true);
  });

  it("every denial a coordinator can meet is worded as whose call it is, not as a refusal", () => {
    const esc = item({ escalation: { rung: "supervisor", level: 2, target: "supervisor", accepted_by: ["supervisor"], milestone: null, at: null } });
    for (const why of [actionState("accept_escalation", "coordinator", esc).why, actionState("take_ownership", "coordinator", item()).why, actionState("record_callback", "patient" as never, item()).why]) {
      expect(why).toBeTruthy();
      expect(why).not.toMatch(/^Only |cannot do this|Your role cannot/i);
    }
  });
});
