/** The Today screen's words and rules (C14): what a queue item says, who may do each one-click action, and how pages merge. */
import type { Role, TodayActionId as TodayAction, TodayAgencyItem, TodayGroup, TodayGroupId, TodayItem } from "../api/contract";
import { BACKEND_ROLE, TODAY_GROUPS } from "../api/contract";
import { can } from "../app/AppContext";
import { STAFF, TODAY } from "../i18n/strings";
import { RUNG_LABEL } from "./pathway";
import { titleCase } from "./format";

export type TodayAnyItem = TodayItem | TodayAgencyItem;
export const isAgencyItem = (i: TodayAnyItem): i is TodayAgencyItem => "task_id" in i;

const days = (n: number | undefined) => (!n ? "today" : `${n} ${n === 1 ? "day" : "days"} ago`);
const BACKEND_ROLE_WORDS: Record<string, string> = {
  coordinator: "a coordinator", supervisor: "a supervisor", primary_care_owner: "the case owner", specialist_owner: "the case owner",
};

/** One sentence, in plain words, for why the case is in this queue. */
export function whyText(item: TodayItem): string {
  const w = item.why;
  switch (w.code) {
    case "urgent_untouched":
      return `An urgent result came in ${days(w.days)} and nobody has acted on it yet.`;
    case "overdue":
      return !w.days ? "It has just passed its deadline." : `It is ${w.days} ${w.days === 1 ? "day" : "days"} past its deadline.`;
    case "escalation_waiting": {
      const rung = (RUNG_LABEL as Record<string, string>)[w.rung ?? ""] ?? titleCase(w.rung ?? "escalation");
      return `It was escalated to ${rung.charAt(0).toLowerCase()}${rung.slice(1)} and a person has to accept it.`;
    }
    case "reply_waiting": {
      const r = (STAFF.reviewReasons as Record<string, string>)[w.reason ?? ""];
      return r ? `${r}.` : "The patient's reply needs a person to read it.";
    }
    case "callback_waiting":
      return "The patient asked for a call-back. Please phone them.";
    case "nobody_looking":
      return w.idle_days ? `Nobody has looked at this case for ${w.idle_days} days.` : "Nobody has looked at this case for a while.";
    case "closure_proposed":
      return "The evidence is on file and a closure is proposed. A physician has to review it and sign.";
    case "declined_signoff":
      return "The patient declined after counselling. A physician who did not counsel them has to sign the closure.";
    default:
      return titleCase(w.code);
  }
}

/** Extra facts about a reply, as short phrases (never the text of the reply). */
export function replyNotes(item: TodayItem): string[] {
  const r = item.reply;
  if (!r) return [];
  const out: string[] = [];
  if (r.forced_reason && r.forced_reason in TODAY.reasons) out.push(TODAY.reasons[r.forced_reason as keyof typeof TODAY.reasons]);
  if (r.confidence !== null && r.confidence !== undefined && r.intent) out.push(`Model confidence ${r.confidence.toFixed(2)} (read as "${titleCase(r.intent)}")`);
  if (r.holds.length) out.push(`${STAFF.holdsLabel}: ${r.holds.map((h) => (STAFF.holds as Record<string, string>)[h] ?? h).join("; ")}`);
  return out;
}

export interface ActionState { allowed: boolean; why: string | null }

/** Whether the acting role may take the action. A UI mirror of the API's policy (the API decides and refuses the rest). */
export function actionState(action: TodayAction, role: Role, item: TodayItem): ActionState {
  const ok = { allowed: true, why: null };
  switch (action) {
    case "open_case":
      return ok;
    case "accept_escalation": {
      const by = item.escalation?.accepted_by ?? [];
      if (can(role, "acceptEscalation") && by.includes(BACKEND_ROLE[role])) return ok;
      const roles = [...new Set(by.map((r) => BACKEND_ROLE_WORDS[r] ?? r))].join(" or ") || "the named role";
      return { allowed: false, why: TODAY.denied.accept_escalation(roles) };
    }
    case "take_ownership":
      return can(role, "takeOwnership") ? ok : { allowed: false, why: TODAY.denied.take_ownership };
    case "confirm_contact":
    case "record_callback":
      return can(role, "contact") ? ok : { allowed: false, why: TODAY.denied.contact };
  }
}

/** Sort key of a queue: longest-waiting first, then by case so a page is stable. */
export function bySince(a: TodayAnyItem, b: TodayAnyItem): number {
  return a.since < b.since ? -1 : a.since > b.since ? 1 : (a.case_id ?? "") < (b.case_id ?? "") ? -1 : 1;
}

const keyOf = (i: TodayAnyItem) => (isAgencyItem(i) ? i.task_id : i.case_id);

/** Add a page of groups to what is already shown: no item twice, longest-waiting first. */
export function mergeGroups(have: Partial<Record<TodayGroupId, TodayAnyItem[]>>, page: TodayGroup[]): Record<TodayGroupId, TodayAnyItem[]> {
  const out = {} as Record<TodayGroupId, TodayAnyItem[]>;
  for (const g of TODAY_GROUPS) out[g] = [...(have[g] ?? [])];
  for (const g of page) {
    const seen = new Set(out[g.group].map(keyOf));
    for (const item of g.items) if (!seen.has(keyOf(item))) { out[g.group].push(item); seen.add(keyOf(item)); }
    out[g.group].sort(bySince);
  }
  return out;
}
