/**
 * Mock adapter: the whole API in the browser, seeded from fixtures.ts.
 * State persists in localStorage so the coordinator and patient views (two tabs, same origin)
 * see each other's actions; "Reset demo data" in the header restores the seed.
 * It mirrors the engine's rules (transitions, closure guard, referral handoff, consent freeze, owner scoping,
 * the draft approval gate) so the UI is exercised realistically. It needs no token, but `login()` records
 * who is acting, because the real API decides who may approve a draft from the bearer token.
 */
import type {
  Alert, ApiClient, CaseDetail, CaseEvent, CaseListParams, CaseSummary, CaseTimeline, ClosureMetrics, Draft, DraftApproval,
  DraftRecord, DraftRejection, DraftRequestBody, Me, PostEventBody, Role, TimelineItem,
} from "./contract";
import { ApiError, BACKEND_ROLE, OWNER_SCOPED_ROLES, ROLES } from "./contract";
import { FIXTURE_RISK, SEED_ALERTS, SEED_METRICS, SIM_NOW, seedDrafts, seedEvents } from "./fixtures";
import { PROVIDER_CLINICIANS, ROLE_NAMES, ROLE_PROVIDERS, backendActor } from "./identities";
import { ESCALATION_LADDER } from "../lib/pathway";
import {
  consentHistoryOf, consentOf, isOwnerScopedActor, overdueDays, pendingEscalation, replayHandoffs, replayState, slaClock, summarise,
  validateAppend,
} from "../lib/derive";

export { ROLE_NAMES };

const KEY = "sc.mock.v2";
const ROLE_KEY = "sc.role";

interface MockDb {
  events: Record<string, CaseEvent[]>;
  drafts: Record<string, DraftRecord>;
  alerts: Alert[];
  draftCounter: number;
  notificationCounter: number;
  tick: number;
}

interface Session {
  role: Role;
  name: string;
  provider?: string;
}

/** A stable 64-hex digest of the prompt inputs (a stand-in for the backend's SHA-256 of system + user prompt). */
function fakePromptHash(input: string): string {
  let out = "";
  for (let round = 0; out.length < 64; round++) {
    let h = 0x811c9dc5 ^ (round * 0x9e3779b1);
    for (let i = 0; i < input.length; i++) h = Math.imul(h ^ input.charCodeAt(i), 0x01000193) >>> 0;
    out += h.toString(16).padStart(8, "0");
  }
  return out.slice(0, 64);
}

const DRAFT_TEXT: Record<DraftRequestBody["language"], Record<DraftRequestBody["purpose"], string>> = {
  en: {
    result_notification: "Hello. Your screening test needs a follow-up camera test called a colonoscopy. This is not a diagnosis. Please call us to book a time.",
    reminder: "Hello. This is a reminder to call us about your screening follow-up. This is not a diagnosis. We are here to help.",
    appointment_confirmation: "Hello. Your follow-up appointment is booked. Please arrive on time and call us if you cannot come.",
  },
  ar: {
    result_notification: "مرحبًا. يحتاج فحصك إلى فحص متابعة بالكاميرا اسمه تنظير القولون. هذا ليس تشخيصًا. يرجى الاتصال بنا لحجز موعد.",
    reminder: "مرحبًا. هذا تذكير بالاتصال بنا بخصوص متابعة فحصك. هذا ليس تشخيصًا. نحن هنا لمساعدتك.",
    appointment_confirmation: "مرحبًا. تم حجز موعد المتابعة. يرجى الحضور في الوقت المحدد والاتصال بنا إذا لم تتمكن من الحضور.",
  },
};

export function createMockAdapter(storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null = safeStorage()): ApiClient {
  let memory: MockDb | null = null;

  const storedRole = (): Role => {
    try {
      const r = storage?.getItem(ROLE_KEY) as Role | null;
      if (r && (ROLES as string[]).includes(r)) return r;
    } catch {
      /* ignore */
    }
    return "coordinator";
  };
  let session: Session = { role: storedRole(), name: "", provider: undefined };
  session = { ...session, name: ROLE_NAMES[session.role], provider: ROLE_PROVIDERS[session.role] };
  const actor = () => backendActor(session.role, session.name);

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
  const fresh = (): MockDb => ({
    events: seedEvents(), drafts: seedDrafts(), alerts: structuredClone(SEED_ALERTS), draftCounter: 0, notificationCounter: 0, tick: 0,
  });

  function must(db: MockDb, id: string): CaseEvent[] {
    const evs = db.events[id];
    if (!evs) throw new ApiError(404, "case not found");
    return evs;
  }
  const summary = (db: MockDb, id: string): CaseSummary => summarise(id, must(db, id), SIM_NOW, FIXTURE_RISK[id] ?? 0.2);
  const detail = (db: MockDb, id: string): CaseDetail => {
    const evs = must(db, id);
    const consent = consentOf(evs);
    return {
      ...summary(db, id),
      consent,
      consent_history: consentHistoryOf(evs),
      review_reason: consent.withdrawn ? "consent_withdrawn" : null,
      events: structuredClone(evs),
    };
  };
  function nextAt(db: MockDb): string {
    db.tick += 1;
    return new Date(new Date(SIM_NOW).getTime() + db.tick * 60_000).toISOString();
  }
  function append(
    db: MockDb, id: string, type: string, who: string, payload: Record<string, unknown>,
    opts: { eventId?: string; internal?: boolean } = {},
  ) {
    const evs = must(db, id);
    if (opts.eventId && evs.some((e) => e.event_id === opts.eventId)) return; // idempotent retry
    validateAppend(evs, type, who, payload, { internal: opts.internal, provider: session.provider });
    evs.push({ seq: evs.length + 1, event_id: opts.eventId, type, at: nextAt(db), actor: who, payload });
  }

  /** Who may approve or reject a draft on `id`: the case's owner (a clinician login), never the coordinator or the patient. */
  function authorizeDecision(db: MockDb, id: string, verb: "approve" | "reject") {
    if (!OWNER_SCOPED_ROLES.includes(session.role)) {
      throw new ApiError(403, `role '${BACKEND_ROLE[session.role]}' may not call POST /drafts/{draft_id}/${verb}`);
    }
    const owner = replayHandoffs(must(db, id)).owner;
    if (!owner || owner.actor !== actor()) {
      throw new ApiError(403, `${BACKEND_ROLE[session.role]} '${session.name}' may only act on cases they own (this case's owner is ${owner?.name ?? "not yet assigned"})`);
    }
  }

  function requireStaff() {
    if (session.role === "patient") throw new ApiError(403, "role 'patient' may not call this route");
  }

  return {
    adapter: "mock",
    async health() {
      return { status: "ok" };
    },
    async login(role, name, provider) {
      session = { role, name, provider: provider ?? ROLE_PROVIDERS[role] };
    },
    async me(): Promise<Me> {
      return { role: session.role, name: session.name, user_id: actor(), provider: session.provider };
    },
    async listCases(params: CaseListParams = {}): Promise<CaseSummary[]> {
      const db = load();
      return Object.keys(db.events)
        .map((id) => summary(db, id))
        .filter((c) => !params.state || c.state === params.state)
        .filter((c) => !params.owner || c.owner === params.owner)
        .filter((c) => !params.overdue || c.overdue)
        .filter((c) => !params.flagged || !!c.watchdog_flagged);
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
      if (clock.due_at && clock.started_at && clock.sla_event) {
        items.push({ kind: "sla_started", at: clock.started_at, sla: clock.sla_event, due_at: clock.due_at });
      }
      if (clock.due_at && lateDays > 0 && clock.sla_event) {
        items.push({ kind: "sla_breached", at: clock.due_at, sla: clock.sla_event, due_at: clock.due_at, breach_days: lateDays });
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
      for (const e of evs) {
        if (e.type === "handoff_rejected" && e.payload.bounced === true) {
          const to = replayHandoffs(evs.slice(0, evs.indexOf(e))).pending?.to_provider;
          items.push({ kind: "bounce", at: e.at, to_provider: to, reason: String(e.payload.reason ?? "timeout") });
        } else if (e.type === "watchdog_flagged") {
          items.push({
            kind: "watchdog", at: e.at, reason: String(e.payload.reason ?? "idle"),
            ...(typeof e.payload.idle_days === "number" ? { idle_days: e.payload.idle_days } : {}),
          });
        }
      }
      items.sort((a, b) => a.at.localeCompare(b.at));
      return { case_id: id, sim_time: SIM_NOW, items };
    },
    async postEvent(id, body: PostEventBody) {
      const db = load();
      const payload = body.payload ?? {};
      const before = replayHandoffs(must(db, id));
      append(db, id, body.type, body.actor, payload, { eventId: body.event_id });
      // The mock plays the receiving provider of a handoff. A clinician asking on their own provider's behalf
      // (not the owner) is left pending: the UI shows "waiting for acceptance".
      if (body.type === "handoff_requested") {
        const provider = String(payload.to_provider ?? "");
        const asksForMine = isOwnerScopedActor(body.actor) && before.owner?.actor !== body.actor;
        const clinician = PROVIDER_CLINICIANS[provider];
        const answerer = clinician ? `${clinician.role}:${clinician.name}` : `provider:${provider}`;
        if (!asksForMine && answerer !== body.actor) {
          if (provider === "Al Noor Endoscopy Centre") {
            append(db, id, "handoff_rejected", answerer, { reason: "capacity" }, { internal: true });
          } else {
            append(db, id, "handoff_accepted", answerer, {}, { internal: true });
          }
        }
      }
      // Withdrawing consent puts the case on the human-review list (the API emits review_requested as system:consent).
      if (body.type === "consent_updated" && body.payload?.withdrawn === true) {
        const evs = must(db, id);
        append(db, id, "review_requested", "system:consent", { reason: "consent_withdrawn", source_event_id: evs.at(-1)?.event_id ?? `seq-${evs.length}` }, { internal: true });
      }
      save(db);
      return detail(db, id);
    },
    async requestDraft(id, body: DraftRequestBody): Promise<Draft> {
      requireStaff();
      const db = load();
      const evs = must(db, id);
      const model = summary(db, id);
      if (model.consent_frozen) throw new ApiError(409, "outreach is frozen: the patient has withdrawn consent");
      if (model.state === "closed") throw new ApiError(409, "the case is closed: no new drafts");
      if (body.purpose === "appointment_confirmation") {
        const booked = [...evs].reverse().find((e) => e.type === "book_confirmatory");
        if (model.state !== "confirmatory_booked" || !booked?.payload.appointment_date) {
          throw new ApiError(409, "no booked appointment date on this case: cannot confirm an appointment");
        }
      }
      db.draftCounter += 1;
      const text = DRAFT_TEXT[body.language][body.purpose];
      const record: DraftRecord = {
        draft_id: `d-mock-${db.draftCounter}`,
        case_id: id,
        purpose: body.purpose,
        channel: body.channel,
        language: body.language,
        text,
        text_ar: body.language === "ar" ? text : null,
        text_en: body.language === "ar" ? null : text,
        provider: "template",
        model_id: null,
        fallback_used: false,
        abstention_reason: null,
        prompt_hash: fakePromptHash(`${id}|${body.purpose}|${body.channel}|${body.language}`),
        created_by: actor(),
        created_at: nextAt(db),
        approved_at: null, approved_by: null, rejected_at: null, rejected_by: null, rejection_reason: null,
      };
      // The event carries no text (D4): the text is read back through listDrafts.
      append(db, id, "message_drafted", `model:${record.provider}`, {
        draft_id: record.draft_id, provider: record.provider, fallback_used: record.fallback_used,
        prompt_hash: record.prompt_hash, language: record.language,
      }, { internal: true });
      db.drafts[record.draft_id] = record;
      save(db);
      return {
        draft_id: record.draft_id, text: record.text, text_ar: record.text_ar, text_en: record.text_en, language: body.language,
        provider: record.provider, fallback_used: record.fallback_used, abstention_reason: record.abstention_reason, prompt_hash: record.prompt_hash,
      };
    },
    async listDrafts(id): Promise<DraftRecord[]> {
      requireStaff();
      const db = load();
      must(db, id);
      return Object.values(db.drafts)
        .filter((d) => d.case_id === id)
        .sort((a, b) => a.created_at.localeCompare(b.created_at))
        .map((d) => structuredClone(d));
    },
    async approveDraft(draftId): Promise<DraftApproval> {
      const db = load();
      const d = db.drafts[draftId];
      if (!d) throw new ApiError(404, "draft not found");
      authorizeDecision(db, d.case_id, "approve");
      if (d.approved_at) throw new ApiError(409, "draft is already approved");
      if (d.rejected_at) throw new ApiError(409, "draft is already rejected");
      const m = summary(db, d.case_id);
      if (m.consent_frozen) throw new ApiError(409, "outreach is frozen: the patient has withdrawn consent");
      if (m.state === "closed") throw new ApiError(409, "the case is closed: nothing to approve");
      const who = actor();
      // One step: the approval is recorded on the draft and as message_approved, or neither.
      append(db, d.case_id, "message_approved", who, { draft_id: draftId }, { internal: true });
      const at = must(db, d.case_id).at(-1)!.at;
      d.approved_at = at;
      d.approved_by = who;
      // Only now does the notification mock run (A7): consent for the channel, then the contact flag, then message_sent.
      db.notificationCounter += 1;
      const notificationId = `n-mock-${db.notificationCounter}`;
      const base = { draft_id: draftId, approved_by: who, approved_at: at };
      const genesis = must(db, d.case_id)[0].payload.contact_valid;
      const consent = consentOf(must(db, d.case_id));
      if (!(consent as unknown as Record<string, boolean>)[`channel_${d.channel}`]) {
        save(db);
        return { ...base, sent: false, blocked_reason: "consent", notification_id: notificationId };
      }
      if (genesis === false || (genesis && typeof genesis === "object" && (genesis as Record<string, boolean>)[d.channel] === false)) {
        save(db);
        return { ...base, sent: false, blocked_reason: "contact", notification_id: notificationId };
      }
      append(db, d.case_id, "message_sent", "system:notifier", {
        draft_id: draftId, notification_id: notificationId, channel: d.channel, language: d.language, purpose: d.purpose,
      }, { internal: true });
      save(db);
      return { ...base, sent: true, blocked_reason: null, notification_id: notificationId };
    },
    async rejectDraft(draftId, reason): Promise<DraftRejection> {
      const db = load();
      const d = db.drafts[draftId];
      if (!d) throw new ApiError(404, "draft not found");
      authorizeDecision(db, d.case_id, "reject");
      const trimmed = (reason ?? "").trim();
      if (!trimmed || trimmed.length > 500) throw new ApiError(422, "reason must be 1-500 characters");
      if (d.approved_at) throw new ApiError(409, "draft is already approved");
      if (d.rejected_at) throw new ApiError(409, "draft is already rejected");
      // Recorded on the draft row only: the event vocabulary has no message_rejected yet (A16).
      d.rejected_at = nextAt(db);
      d.rejected_by = actor();
      d.rejection_reason = trimmed;
      save(db);
      return { draft_id: draftId, rejected_by: d.rejected_by, rejected_at: d.rejected_at, reason: trimmed };
    },
    async closureMetrics(): Promise<ClosureMetrics> {
      return { ...SEED_METRICS, sim_time: SIM_NOW };
    },
    async listAlerts(): Promise<Alert[]> {
      return structuredClone(load().alerts);
    },
    async setSchedulerDown(down) {
      const db = load();
      const active = db.alerts.find((a) => a.kind === "scheduler_down" && a.cleared_at === null);
      if (down && !active) db.alerts.push({ kind: "scheduler_down", since: new Date(new Date(SIM_NOW).getTime() - 120_000).toISOString(), cleared_at: null });
      if (!down && active) active.cleared_at = SIM_NOW;
      save(db);
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
