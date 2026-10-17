/**
 * Mock adapter: the whole API in the browser, seeded from fixtures.ts.
 * State persists in localStorage so the coordinator and patient views (two tabs, same origin)
 * see each other's actions; "Reset demo data" in the header restores the seed.
 * It mirrors the engine's rules (transitions, closure guard, clinician-only reopen) so the UI is exercised realistically.
 * Auth is ignored: login() is a no-op and no token is ever required.
 */
import type {
  ApiClient, CaseDetail, CaseEvent, CaseListParams, CaseSummary, CaseTimeline, ClosureMetrics, Draft,
  DraftApproval, Me, PostEventBody, Role, TimelineItem,
} from "./contract";
import { ApiError } from "./contract";
import { FIXTURE_RISK, SEED_METRICS, SIM_NOW, seedEvents } from "./fixtures";
import { ESCALATION_LADDER } from "../lib/pathway";
import { overdueDays, pendingEscalation, replayState, slaClock, summarise, validateAppend } from "../lib/derive";

const KEY = "sc.mock.v1";
const ROLE_KEY = "sc.role";

interface MockDb {
  events: Record<string, CaseEvent[]>;
  drafts: Record<string, { case_id: string; approved?: DraftApproval }>;
  draftCounter: number;
  tick: number;
}

export const ROLE_NAMES: Record<Role, string> = {
  coordinator: "Maryam Al Suwaidi",
  owner: "Dr. Layla Haddad",
  specialist: "Dr. Karim Nasser",
  clinician: "Dr. Huda Rahman",
  patient: "Synthetic Patient",
};

export function createMockAdapter(storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null = safeStorage()): ApiClient {
  let memory: MockDb | null = null;

  function load(): MockDb {
    if (storage) {
      try {
        const raw = storage.getItem(KEY);
        if (raw) return JSON.parse(raw) as MockDb;
      } catch {
        /* fall through to seed */
      }
    }
    if (!memory) memory = fresh();
    if (storage) save(memory);
    return memory;
  }
  function save(db: MockDb) {
    memory = db;
    try {
      storage?.setItem(KEY, JSON.stringify(db));
    } catch {
      /* quota or private mode: keep in memory */
    }
  }
  const fresh = (): MockDb => ({ events: seedEvents(), drafts: {}, draftCounter: 0, tick: 0 });

  function must(db: MockDb, id: string): CaseEvent[] {
    const evs = db.events[id];
    if (!evs) throw new ApiError(404, "case not found");
    return evs;
  }
  const detail = (db: MockDb, id: string): CaseDetail => {
    const evs = must(db, id);
    return { ...summarise(id, evs, SIM_NOW, FIXTURE_RISK[id] ?? 0.2), events: structuredClone(evs) };
  };
  function append(db: MockDb, id: string, type: string, actor: string, payload: Record<string, unknown>, eventId?: string) {
    const evs = must(db, id);
    if (eventId && evs.some((e) => e.event_id === eventId)) return; // idempotent retry
    validateAppend(replayState(evs), type, actor, payload);
    db.tick += 1;
    evs.push({
      seq: evs.length + 1,
      event_id: eventId,
      type,
      at: new Date(new Date(SIM_NOW).getTime() + db.tick * 60_000).toISOString(),
      actor,
      payload,
    });
  }

  return {
    adapter: "mock",
    async health() {
      return { status: "ok" };
    },
    async login() {
      /* the mock ignores auth */
    },
    async me(): Promise<Me> {
      let role: Role = "coordinator";
      try {
        const r = storage?.getItem(ROLE_KEY) as Role | null;
        if (r && r in ROLE_NAMES) role = r;
      } catch {
        /* ignore */
      }
      return { role, name: ROLE_NAMES[role] };
    },
    async listCases(params: CaseListParams = {}): Promise<CaseSummary[]> {
      const db = load();
      return Object.entries(db.events)
        .map(([id, evs]) => summarise(id, evs, SIM_NOW, FIXTURE_RISK[id] ?? 0.2))
        .filter((c) => !params.state || c.state === params.state)
        .filter((c) => !params.owner || c.owner === params.owner)
        .filter((c) => !params.overdue || c.overdue);
    },
    async getCase(id) {
      return detail(load(), id);
    },
    async getTimeline(id): Promise<CaseTimeline> {
      const db = load();
      const evs = must(db, id);
      const items: TimelineItem[] = evs.map((e) => ({ kind: "event" as const, ...e }));
      const state = replayState(evs);
      const clock = slaClock(evs, state, SIM_NOW);
      const lateDays = overdueDays(clock.due_at, SIM_NOW);
      if (clock.due_at && lateDays > 0 && clock.sla_event) {
        items.push({ kind: "sla_breach", at: clock.due_at, sla: clock.sla_event, due_at: clock.due_at, breach_days: lateDays });
        const dueAt = clock.due_at;
        const accepted = evs.some((e) => e.type === "escalation_accepted" && e.at >= dueAt);
        for (const rung of ESCALATION_LADDER) {
          if (lateDays >= rung.after_breach_days) {
            items.push({
              kind: "escalation",
              at: new Date(new Date(dueAt).getTime() + rung.after_breach_days * 86_400_000).toISOString(),
              rung: rung.rung,
              target_role: rung.target_role,
              after_breach_days: rung.after_breach_days,
              accepted: rung.rung === "reminder" ? undefined : accepted && !pendingEscalation(evs),
            });
          }
        }
      }
      items.sort((a, b) => a.at.localeCompare(b.at));
      return { case_id: id, sim_time: SIM_NOW, items };
    },
    async postEvent(id, body: PostEventBody) {
      const db = load();
      const payload = body.payload ?? {};
      append(db, id, body.type, body.actor, payload, body.event_id);
      // Simulated provider reply to a hand-off (the first provider always rejects: reason "capacity").
      if (body.type === "handoff_requested") {
        const provider = String(payload.to_provider ?? "");
        if (provider === "Al Noor Endoscopy Centre") {
          append(db, id, "handoff_rejected", `provider:${provider}`, { reason: "capacity" });
        } else {
          append(db, id, "handoff_accepted", `provider:${provider}`, {});
        }
      }
      save(db);
      return detail(db, id);
    },
    async requestDraft(id): Promise<Draft> {
      const db = load();
      must(db, id);
      db.draftCounter += 1;
      const draft: Draft = {
        draft_id: `d-mock-${db.draftCounter}`,
        provider: "template-fallback",
        fallback_used: true,
        text_en: "Hello. Your screening test needs a follow-up camera test called a colonoscopy. This is not a diagnosis. Please call us to book a time.",
        text_ar: "مرحبًا. يحتاج فحصك إلى فحص متابعة بالكاميرا اسمه تنظير القولون. هذا ليس تشخيصًا. يرجى الاتصال بنا لحجز موعد.",
      };
      append(db, id, "message_drafted", `model:${draft.provider}`, {
        draft_id: draft.draft_id, provider: draft.provider, fallback_used: draft.fallback_used,
        text_en: draft.text_en, text_ar: draft.text_ar,
      });
      db.drafts[draft.draft_id] = { case_id: id };
      save(db);
      return draft;
    },
    async approveDraft(draftId, approvedBy): Promise<DraftApproval> {
      const db = load();
      const d = db.drafts[draftId];
      if (!d) throw new ApiError(404, "draft not found");
      const role = (Object.keys(ROLE_NAMES) as Role[]).find((r) => ROLE_NAMES[r] === approvedBy) ?? "human";
      append(db, d.case_id, "message_approved", `${role}:${approvedBy}`, { draft_id: draftId });
      const approval = { draft_id: draftId, approved_by: approvedBy, approved_at: db.events[d.case_id].at(-1)!.at };
      // The backend sends the message only after the approval has been recorded.
      append(db, d.case_id, "message_sent", "system:sender", { draft_id: draftId });
      d.approved = approval;
      save(db);
      return approval;
    },
    async closureMetrics(): Promise<ClosureMetrics> {
      return { ...SEED_METRICS, sim_time: SIM_NOW };
    },
    async resetDemo() {
      save(fresh());
    },
  };
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}
