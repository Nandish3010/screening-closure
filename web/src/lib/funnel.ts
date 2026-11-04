/**
 * The outcome funnel on the web (A39, ADR-052): the mock adapter's answer to `GET /metrics/funnel`, and the helpers the card uses.
 *
 * The rules are those of api/app/metrics_funnel.py, applied to the event logs the mock holds:
 *  - result recorded: the case exists;
 *  - prioritised: the case carries a result priority tier (a pathway with priority rules; the bowel pack has none);
 *  - referred: the first `handoff_requested` or `book_confirmatory`;
 *  - attended: the first `complete_confirmatory`;
 *  - outcome documented: the first SAFE `close` (a human, an evidence reference and a signature) that is not an informed refusal.
 *    A refusal closed `declined` is counted apart.
 *  - at risk of being lost: no confirmatory completed, no refusal on record, and the contact marked lost or no pathway progress for
 *    30 days at the simulated "now" (the closure metrics' `lost_to_follow_up` rule).
 * Medians are of calendar days between the instants, over the cases that reached both ends. Synthetic data, simulated clock.
 */
import type { CaseEvent, FunnelAtRiskCase, FunnelMetrics, FunnelParams, FunnelStep, FunnelStepKey } from "../api/contract";
import { closureOutcomeOf, isAutomatedActor, refusalOf, replayState } from "./derive";
import { TRANSITIONS } from "./pathway";
import { PRIORITY_TIERS } from "./priority";

const DAY = 86_400_000;
export const LOST_IDLE_DAYS = 30;
export const AT_RISK_LIMIT = 10;
export const UNRANKED = "unranked";

/** The five steps in the programme owner's words, with what each one counts. Mirrors `STEPS` in api/app/metrics_funnel.py. */
export const FUNNEL_STEPS: { key: FunnelStepKey; label: string; from: FunnelStepKey | null; definition: string }[] = [
  { key: "recorded", label: "Result recorded", from: null, definition: "A screening result is recorded and linked to the person: a case exists." },
  { key: "prioritised", label: "Higher-risk people prioritised", from: "recorded", definition: "A result priority tier was recorded at intake. Only pathways with priority rules record one." },
  { key: "referred", label: "Referred to the appropriate service", from: "recorded", definition: "A referral to the service was requested, or the appointment was booked, whichever came first." },
  { key: "attended", label: "Attended", from: "referred", definition: "The confirmatory procedure or visit took place and was recorded, after reminders and navigation." },
  { key: "documented", label: "Outcome documented", from: "attended", definition: "Closed with evidence: a person signed the close and the report is on record. Informed refusals are counted apart." },
];

const PROGRESS_EVENTS = new Set(TRANSITIONS.map((t) => t.event));

export interface FunnelCase {
  id: string;
  events: CaseEvent[];
  pathway: string;
  /** The result priority tier, or null (not ranked). */
  priority: string | null;
}

const ms = (iso: string) => Date.parse(iso);
const first = (events: CaseEvent[], pred: (e: CaseEvent) => boolean) => events.find(pred)?.at ?? null;

export function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

interface Facts {
  c: FunnelCase;
  t0: string;
  referredAt: string | null;
  attendedAt: string | null;
  documentedAt: string | null;
  declined: boolean;
  atRisk: boolean;
  since: string;
  lost: boolean;
}

function factsOf(c: FunnelCase, simNow: string): Facts {
  const evs = c.events;
  const t0 = evs[0]!.at;
  const referredAt = first(evs, (e) => e.type === "handoff_requested" || e.type === "book_confirmatory");
  const attendedAt = first(evs, (e) => e.type === "complete_confirmatory");
  const safeClose = evs.find((e) => e.type === "close" && !isAutomatedActor(e.actor) && !!e.payload?.evidence_ref && !!e.payload?.signed_by);
  const safeDeclined = !!safeClose && safeClose.payload?.outcome === "declined";
  const lost = evs.some((e) => e.type === "mark_lost_contact");
  const lastProgress = [...evs].reverse().find((e) => PROGRESS_EVENTS.has(e.type))?.at ?? null;
  const since = lastProgress ?? t0;
  const idleDays = (ms(simNow) - ms(since)) / DAY;
  const refused = refusalOf(evs) !== null || closureOutcomeOf(evs) === "declined";
  return {
    c, t0, referredAt, attendedAt,
    documentedAt: safeClose && !safeDeclined ? safeClose.at : null,
    declined: safeDeclined,
    atRisk: attendedAt === null && !refused && (lost || idleDays >= LOST_IDLE_DAYS),
    since, lost,
  };
}

/** `GET /metrics/funnel` over a set of cases (the mock's answer). `pathways` are the packs present, for the filter options. */
export function funnelMetricsOf(cases: FunnelCase[], simTime: string, params: FunnelParams, pathways: string[]): FunnelMetrics {
  const tiers = [...PRIORITY_TIERS].filter((t) => cases.some((c) => c.priority === t));
  const facts = cases
    .filter((c) => !params.pathway || c.pathway === params.pathway)
    .filter((c) => !params.priority || (params.priority === UNRANKED ? c.priority === null : c.priority === params.priority))
    .map((c) => factsOf(c, simTime));
  const reached: Record<FunnelStepKey, (f: Facts) => boolean> = {
    recorded: () => true,
    prioritised: (f) => f.c.priority !== null,
    referred: (f) => f.referredAt !== null,
    attended: (f) => f.attendedAt !== null,
    documented: (f) => f.documentedAt !== null,
  };
  const days = (a: string | null, b: string | null) => (a && b ? (ms(b) - ms(a)) / DAY : null);
  const gap: Partial<Record<FunnelStepKey, (f: Facts) => number | null>> = {
    referred: (f) => days(f.t0, f.referredAt),
    attended: (f) => days(f.referredAt, f.attendedAt),
    documented: (f) => days(f.attendedAt, f.documentedAt),
  };
  const counts = Object.fromEntries(FUNNEL_STEPS.map((s) => [s.key, facts.filter(reached[s.key]).length])) as Record<FunnelStepKey, number>;
  const steps: FunnelStep[] = FUNNEL_STEPS.map((s) => {
    const xs = (gap[s.key] ? facts.map(gap[s.key]!).filter((x): x is number => x !== null) : []);
    const prev = s.from ? counts[s.from] : 0;
    return {
      key: s.key, label: s.label, definition: s.definition, count: counts[s.key],
      share_of_recorded: counts.recorded ? Math.round((counts[s.key] / counts.recorded) * 1e4) / 1e4 : null,
      from: s.from,
      conversion: s.from && prev ? Math.round((counts[s.key] / prev) * 1e4) / 1e4 : null,
      median_days: median(xs), median_n: xs.length,
    };
  });
  const risk = facts.filter((f) => f.atRisk).sort((a, b) => ms(a.since) - ms(b.since) || (a.c.id < b.c.id ? -1 : 1));
  const listed: FunnelAtRiskCase[] = risk.slice(0, AT_RISK_LIMIT).map((f) => ({
    case_id: f.c.id, pathway: f.c.pathway, priority: f.c.priority, state: replayState(f.c.events),
    idle_days: Math.round(((ms(simTime) - ms(f.since)) / DAY) * 10) / 10,
    reason: f.lost ? "contact_lost" : "no_progress",
  }));
  return {
    sim_time: simTime,
    filters: { pathway: params.pathway ?? null, priority: params.priority ?? null },
    options: { pathways, priorities: [...tiers, UNRANKED] },
    steps,
    declined_closures: facts.filter((f) => f.declined).length,
    at_risk: { count: risk.length, idle_days_threshold: LOST_IDLE_DAYS, cases: listed, truncated: risk.length > listed.length, limit: AT_RISK_LIMIT },
    replay_failures: { count: 0 },
  };
}

/** A fraction as a whole percent, or an en dash when there is nothing to divide. */
export function pctOf(x: number | null | undefined): string {
  return x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`;
}

/** Median days to one decimal: "n/a" when no case reached both ends, otherwise "0.4 days", "1 day", "12.5 days". */
export function daysOf(x: number | null | undefined): string {
  if (x === null || x === undefined) return "n/a";
  const r = Math.round(x * 10) / 10;
  return r === 1 ? "1 day" : `${r} days`;
}
