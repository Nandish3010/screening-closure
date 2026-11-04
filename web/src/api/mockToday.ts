/**
 * `GET /today` for the mock adapter (C14): the same seven queues as api/app/today.py, from the same facts of a case (its
 * events and derived fields), so the screen behaves the same with and without the API. One page holds everything the seeded
 * cases give (the mock has dozens of cases, not millions), so `cursor` is always null and `complete` always true.
 */
import type {
  AgencyInboxItem, CaseDetail, TodayActionId, TodayAgencyItem, TodayGroup, TodayGroupId, TodayItem, TodayParams, TodayResponse,
} from "./contract";
import { TODAY_CASE_GROUPS, TODAY_GROUPS } from "./contract";
import { TRIAGE_ACTOR, hasCallbackPending, isAutomatedActor, pendingEscalation } from "../lib/derive";
import { transitionFor } from "../lib/pathway";

const ACCEPTED_BY: Record<string, string[]> = {
  reminder: ["primary_care_owner", "specialist_owner"], supervisor: ["supervisor"], coordinator: ["coordinator"],
};
const LEVEL: Record<string, number> = { reminder: 1, supervisor: 2, coordinator: 3 };

const wholeDays = (from: string | null | undefined, now: string) =>
  from ? Math.max(0, Math.floor((new Date(now).getTime() - new Date(from).getTime()) / 86_400_000)) : 0;

/** A person wrote an event after the latest triage flag. By position in the log, as the API does. */
function actedAfterFlag(d: CaseDetail): boolean {
  let flag = -1;
  d.events.forEach((e, i) => { if (e.type === "review_requested" && e.actor === TRIAGE_ACTOR) flag = i; });
  return flag >= 0 && d.events.slice(flag + 1).some((e) => !isAutomatedActor(e.actor));
}

function latestClassification(d: CaseDetail) {
  for (let i = d.events.length - 1; i >= 0; i--) {
    const e = d.events[i]!;
    if (e.type === "reply_classified" && e.payload.needs_human === true) return e.payload;
  }
  return null;
}

/** The queues one case is in now, as items (empty for a closed case). */
export function todayMemberships(d: CaseDetail, now: string): Partial<Record<TodayGroupId, TodayItem>> {
  if (d.state === "closed") return {};
  const out: Partial<Record<TodayGroupId, TodayItem>> = {};
  const base = {
    case_id: d.case_id, pathway: d.pathway ?? "colorectal_fit", state: d.state, priority: d.priority ?? null,
    owner: d.owner_actor ? { actor: d.owner_actor, display_name: d.owner ?? d.owner_actor, role: d.owner_role ?? "" } : null,
    provider: d.provider ?? null, due_at: d.due_at, last_event_at: d.last_event_at,
  };
  const created = d.events[0]?.at ?? d.created_at ?? now;
  const canTake = !!transitionFor(d.state, "accept_ownership");
  const put = (g: TodayGroupId, since: string, why: TodayItem["why"], actions: TodayActionId[], extra: Partial<TodayItem> = {}) => {
    out[g] = { ...base, since, why, actions, ...extra };
  };

  const untouched = d.events.slice(1).every((e) => isAutomatedActor(e.actor));
  if (d.priority && d.priority.rank === 0 && untouched) {
    put("urgent", created, { code: "urgent_untouched", tier: d.priority.tier, days: wholeDays(created, now) }, ["open_case", ...(canTake ? ["take_ownership" as const] : [])]);
  }
  if (d.overdue) {
    const days = d.due_at ? wholeDays(d.due_at, now) : 0;
    put("overdue", d.due_at ?? created, { code: "overdue", days }, ["open_case"], { days_overdue: days });
  }
  const esc = pendingEscalation(d.events);
  if (esc) {
    put("escalations", esc.at, { code: "escalation_waiting", rung: esc.rung, target: esc.target_role, level: LEVEL[esc.rung] ?? 0 }, ["accept_escalation", "open_case"], {
      escalation: { rung: esc.rung, level: LEVEL[esc.rung] ?? 0, target: esc.target_role, accepted_by: ACCEPTED_BY[esc.rung] ?? [], milestone: null, at: esc.at },
    });
  }
  const flag = d.triage_review_reason ?? null;
  const waitingFlag = flag !== null && !actedAfterFlag(d);
  const callback = hasCallbackPending(d.events);
  if (waitingFlag || callback) {
    const cls = waitingFlag ? latestClassification(d) : null;
    const actions: TodayActionId[] = ["open_case"];
    if (waitingFlag && flag === "wrong_number") actions.unshift("confirm_contact");
    if (!waitingFlag && callback && transitionFor(d.state, "contact_patient")) actions.unshift("record_callback");
    const lastCallback = [...d.events].reverse().find((e) => e.type === "callback_requested");
    put("replies", (waitingFlag ? d.triage_review_requested_at : lastCallback?.at) ?? created,
      { code: waitingFlag ? "reply_waiting" : "callback_waiting", reason: waitingFlag ? flag : (lastCallback?.payload.reason as string | undefined) ?? null }, actions, {
        reply: {
          reason: waitingFlag ? flag : null, callback, holds: [...(d.outreach_holds ?? [])],
          intent: cls ? String(cls.intent) : null, confidence: cls && typeof cls.confidence === "number" ? cls.confidence : null,
          forced_reason: cls && typeof cls.forced_reason === "string" ? cls.forced_reason : null,
        },
      });
  }
  if (d.watchdog_flagged) {
    const wf = d.watchdog_flagged;
    put("flagged", wf.at, { code: "nobody_looking", reason: wf.reason, idle_days: wf.idle_days ?? null }, ["open_case", ...(canTake ? ["take_ownership" as const] : [])], {
      flag: { reason: wf.reason, idle_days: wf.idle_days ?? null, at: wf.at },
    });
  }
  if (d.closure_proposal) {
    put("signatures", d.closure_proposal.proposed_at, { code: "closure_proposed", evidence_ref: d.closure_proposal.evidence_ref }, ["open_case"]);
  } else if (d.state === "declined_pending_signoff") {
    put("signatures", d.refusal?.recorded_at ?? created, { code: "declined_signoff" }, ["open_case"]);
  }
  return out;
}

export function todayOf(details: CaseDetail[], inbox: AgencyInboxItem[], params: TodayParams, now: string): TodayResponse {
  const limit = Math.min(Math.max(params.limit ?? 10, 1), 50);
  const wanted = (params.groups?.length ? params.groups : TODAY_GROUPS) as readonly TodayGroupId[];
  const lists: Record<string, TodayItem[]> = Object.fromEntries(TODAY_CASE_GROUPS.map((g) => [g, [] as TodayItem[]]));
  for (const d of details) {
    for (const [g, item] of Object.entries(todayMemberships(d, now)) as [TodayGroupId, TodayItem][]) lists[g]!.push(item);
  }
  const byId = new Map(details.map((d) => [d.case_id, d]));
  const agency: TodayAgencyItem[] = [...inbox].reverse().slice(0, limit).map((t) => {
    const d = t.case_id ? byId.get(t.case_id) : undefined;
    return {
      task_id: t.task_id, case_id: t.case_id, department: t.department, agent_type: t.agent_type, assignee_role: t.assignee_role,
      title: t.title, summary: t.summary, recommended_action: t.recommended_action, since: t.created_at,
      pathway: d?.pathway ?? null, state: d?.state ?? null, priority: d?.priority ?? null, actions: t.case_id ? ["open_case"] : [],
    };
  });
  const groups: TodayGroup[] = [];
  for (const g of TODAY_GROUPS) {
    if (!wanted.includes(g)) continue;
    if (g === "agency") {
      groups.push({ group: g, items: agency, count: agency.length, more: inbox.length > limit });
    } else {
      const items = lists[g]!.sort((a, b) => (a.since < b.since ? -1 : a.since > b.since ? 1 : a.case_id < b.case_id ? -1 : 1)).slice(0, limit);
      groups.push({ group: g, items, count: items.length, more: lists[g]!.length > limit });
    }
  }
  return { sim_time: now, limit, scanned: details.length, cursor: null, complete: true, agency_cursor: null, groups };
}
