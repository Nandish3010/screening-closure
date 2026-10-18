/**
 * Mock adapter: the whole API in the browser, seeded from fixtures.ts.
 * State persists in localStorage so the coordinator and patient views (two tabs, same origin)
 * see each other's actions; "Reset demo data" in the header restores the seed.
 * It mirrors the engine's rules (transitions, closure guard, referral handoff, consent freeze, owner scoping,
 * the draft approval gate) so the UI is exercised realistically. It needs no token, but `login()` records
 * who is acting, because the real API decides who may approve a draft from the bearer token.
 */
import type {
  Alert, ApiClient, CaseDetail, CaseEvent, CaseListParams, CaseSummary, CaseTimeline, ClosureMetrics, ConfirmatoryResult, Draft, DraftApproval,
  DraftRecord, DraftRejection, DraftRequestBody, EffortMetrics, Me, PostEventBody, Role, TimelineItem,
} from "./contract";
import { ApiError, AUTOMATION_ACTOR, BACKEND_ROLE, OWNER_SCOPED_ROLES, REJECT_NOTE_MAX, REJECT_REASONS, ROLES } from "./contract";
import { FIXTURE_RISK, SEED_ALERTS, SEED_METRICS, SIM_NOW, caseIdFor, seedDrafts, seedEvents } from "./fixtures";
import { PROVIDER_CLINICIANS, ROLE_NAMES, ROLE_PROVIDERS, backendActor } from "./identities";
import { ESCALATION_LADDER } from "../lib/pathway";
import {
  actorKindOf, actorRole, closureProposalOf, completeDetail, consentOf, consentHistoryOf, milestonesOf, overdueDays, pendingEscalation, replayHandoffs,
  replayState, reviewReasonOf, slaClock, summarise, validateAppend,
} from "../lib/derive";
import { effortMetricsOf, effortOf } from "../lib/effort";

export { ROLE_NAMES };

/**
 * Mock options.
 *  automation  A21: mirror the backend's automation actor (`system:automation`): it acknowledges and assigns a new case,
 *              sends the pre-approved result template once the owner accepts, re-routes a declined or bounced referral and
 *              flags the case when the list runs out, and, when the colonoscopy evidence is found, records the result and
 *              proposes closure. Default OFF, so a test that builds its own adapter keeps the manual flow; the shipped app
 *              turns it on (see `mockAutomationFromEnv` in index.ts). A human still accepts ownership and signs off.
 *  decliningProviders  the providers the simulated receiving side declines (reason "capacity"); default Al Noor Endoscopy Centre.
 */
export interface MockOptions {
  automation?: boolean;
  decliningProviders?: readonly string[];
}

/** The automatic re-route list (A21): who is asked next, in order, skipping providers already asked on the case. */
export const REROUTE_LIST: readonly string[] = ["Corniche Gastro Unit", "Al Noor Endoscopy Centre", "Marina Digestive Health"];
/** The dev seed owner a new case is assigned to, and the organisation they act for (A21). */
const ASSIGNED_OWNER = { role: "owner", provider: "Riverside Primary Care" } as const;

const KEY_BASE = "sc.mock.v3";
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

/**
 * Demo draft texts. E14: they follow the API templates (api/app/ai/templates): the result is "ready", the care
 * team needs to talk with the patient about the next step, and nothing states or hints at what the screening found.
 */
export const DRAFT_TEXT: Record<DraftRequestBody["language"], Record<DraftRequestBody["purpose"], string>> = {
  en: {
    result_notification: "Your screening result is ready and your care team needs to talk with you about the next step. Your care team will call you, or you can reply to book a time. The next step is a camera test called a colonoscopy.",
    reminder: "Reminder from your care team. Your screening result is ready and your care team still needs to talk with you about the next step. Your care team will call you, or you can reply to book a time.",
    appointment_confirmation: "Your appointment is booked. Your care team will contact you with the details. Please call us if you cannot come.",
  },
  ar: {
    result_notification: "نتيجة فحصك جاهزة، ويحتاج فريق رعايتك إلى التحدث معك عن الخطوة التالية، وهي فحص بالكاميرا اسمه تنظير القولون. سيتصل بك فريق رعايتك، أو يمكنك الرد على هذه الرسالة لحجز موعد.",
    reminder: "تذكير من فريق رعايتك. نتيجة فحصك جاهزة، ولا يزال فريق رعايتك بحاجة إلى التحدث معك عن الخطوة التالية. سيتصل بك فريق رعايتك، أو يمكنك الرد على هذه الرسالة لحجز موعد.",
    appointment_confirmation: "تم تأكيد موعدك. سيتواصل معك فريق رعايتك بالتفاصيل. يرجى الاتصال بنا إذا لم تتمكن من الحضور.",
  },
};

/**
 * What a patient may see of an event (SEC-2): the allow-list of `readmodel.PATIENT_EVENT_FIELDS` in the API. An event type that
 * is not listed is hidden; for a listed one only these payload keys are kept, and the actor is reduced to its role.
 */
export const PATIENT_EVENT_FIELDS: Record<string, readonly string[]> = {
  case_created: [], acknowledge: [], accept_ownership: [], contact_patient: ["channel"], patient_acknowledge: ["lang"],
  book_confirmatory: ["appointment_date", "appointment_at"], complete_confirmatory: [], clinician_review: [], close: [], reopen: [],
  consent_updated: ["channel_sms", "channel_whatsapp", "channel_phone", "channel_letter", "share_with_specialist", "withdrawn"],
  callback_requested: ["lang"], message_sent: ["channel", "language", "purpose"],
};
export const PATIENT_CONSENT_KEYS = ["channel_sms", "channel_whatsapp", "channel_phone", "channel_letter", "share_with_specialist", "withdrawn"] as const;

/** The events a patient may see, redacted. */
export function patientEvents(events: CaseEvent[]): CaseEvent[] {
  return events.flatMap((e) => {
    const allowed = PATIENT_EVENT_FIELDS[e.type];
    if (!allowed) return [];
    return [{
      seq: e.seq, event_id: e.event_id, type: e.type, at: e.at, actor: actorRole(e.actor),
      payload: Object.fromEntries(Object.entries(e.payload).filter(([k]) => allowed.includes(k))),
    }];
  });
}

/** Every staff-facing event says who wrote it (A21): the API's `actor_kind`. */
const withKind = (e: CaseEvent): CaseEvent => ({ ...e, actor_kind: actorKindOf(e.actor) });

export function createMockAdapter(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null = safeStorage(),
  options: MockOptions = {},
): ApiClient {
  const automation = options.automation === true;
  const declining = new Set(options.decliningProviders ?? ["Al Noor Endoscopy Centre"]);
  // A demo with automation has its own seed (cases the automation actor already worked on), so it keeps its own copy of the data.
  const KEY = automation ? `${KEY_BASE}.auto` : KEY_BASE;
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
    events: seedEvents(automation), drafts: seedDrafts(), alerts: structuredClone(SEED_ALERTS), draftCounter: 0, notificationCounter: 0, tick: 0,
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
      review_reason: reviewReasonOf(evs),
      closure_proposal: closureProposalOf(evs),
      effort: effortOf(evs),
      milestones: milestonesOf(evs, SIM_NOW),
      events: structuredClone(evs).map(withKind),
    };
  };
  /**
   * `GET /cases/{id}` for a patient login (SEC-2), mirroring `readmodel.patient_case_json`: the owner by role only, no staff
   * names or ids, no SLA, escalation, risk or milestone fields, and only the allow-listed events and payload keys with the
   * actor reduced to its role. It goes through `completeDetail` like the http adapter's answer does.
   */
  const patientDetail = (db: MockDb, id: string): CaseDetail => {
    const evs = must(db, id);
    const row = summary(db, id);
    const consent = consentOf(evs) as unknown as Record<string, unknown>;
    const events = patientEvents(evs);
    const ownerRole = row.owner_actor ? actorRole(row.owner_actor) : null;
    const roleOnly = (role: string) => ({ actor: role, display_name: role, role });
    const wire = {
      case_id: id, pathway: row.pathway, state: row.state, created_at: row.created_at, updated_at: row.updated_at,
      owner: ownerRole ? roleOnly(ownerRole) : null,
      consent_frozen: row.consent_frozen, last_event_at: row.last_event_at, risk_score: null,
      read_model: {
        state: row.state, consent_frozen: row.consent_frozen, last_event_at: row.last_event_at,
        consent: Object.fromEntries(PATIENT_CONSENT_KEYS.map((k) => [k, consent[k]])),
      },
      actors: Object.fromEntries([...new Set(events.map((e) => e.actor))].map((r) => [r, roleOnly(r)])),
      events, risk: null,
    };
    return completeDetail(wire as unknown as CaseDetail, SIM_NOW);
  };
  const caseFor = (db: MockDb, id: string): CaseDetail => (session.role === "patient" ? patientDetail(db, id) : detail(db, id));
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

  /** Owner scoping (A5, SEC-2): a clinician login acts, and reads drafts, only on a case it owns. Other roles pass. */
  function requireOwnerScope(db: MockDb, id: string) {
    if (!OWNER_SCOPED_ROLES.includes(session.role)) return;
    const owner = replayHandoffs(must(db, id)).owner;
    if (!owner || owner.actor !== actor()) {
      throw new ApiError(403, `${BACKEND_ROLE[session.role]} '${session.name}' may only act on cases they own (this case's owner is ${owner?.name ?? "not yet assigned"})`);
    }
  }

  /** Who may approve or reject a draft on `id`: the case's owner (a clinician login), never the coordinator or the patient. */
  function authorizeDecision(db: MockDb, id: string, verb: "approve" | "reject") {
    if (!OWNER_SCOPED_ROLES.includes(session.role)) {
      throw new ApiError(403, `role '${BACKEND_ROLE[session.role]}' may not call POST /drafts/{draft_id}/${verb}`);
    }
    requireOwnerScope(db, id);
  }

  function requireStaff() {
    if (session.role === "patient") throw new ApiError(403, "role 'patient' may not call this route");
  }

  // ---- A21: the automation actor. Every step is `system:automation` with `auto: true`; none of them decides anything. ----
  let autoCounter = 0;
  function appendAuto(db: MockDb, id: string, type: string, payload: Record<string, unknown>) {
    autoCounter += 1;
    append(db, id, type, AUTOMATION_ACTOR, { ...payload, auto: true }, { internal: true, eventId: `ev-auto-${must(db, id).length + 1}-${autoCounter}` });
  }

  /** The simulated receiving side of a handoff: it declines the providers in `declining`, the others accept. */
  function playProvider(db: MockDb, id: string, provider: string, requester: string) {
    const clinician = PROVIDER_CLINICIANS[provider];
    const answerer = clinician ? `${clinician.role}:${clinician.name}` : `provider:${provider}`;
    if (answerer === requester) return;
    if (declining.has(provider)) append(db, id, "handoff_rejected", answerer, { reason: "capacity" }, { internal: true });
    else append(db, id, "handoff_accepted", answerer, {}, { internal: true });
  }

  /** A rejected or bounced referral is re-routed to the next provider on the list; when none is left a human is asked to reassign. */
  function autoReroute(db: MockDb, id: string) {
    for (let guard = 0; guard <= REROUTE_LIST.length; guard++) {
      const evs = must(db, id);
      if (replayState(evs) !== "referral_rejected" || consentOf(evs).withdrawn) return; // outreach is frozen: a human decides
      const asked = new Set(evs.filter((e) => e.type === "handoff_requested").map((e) => String(e.payload.to_provider)));
      const next = REROUTE_LIST.find((p) => !asked.has(p));
      if (!next) {
        const last = evs.at(-1)!;
        appendAuto(db, id, "review_requested", { reason: "reroute_exhausted", source_event_id: last.event_id ?? `seq-${last.seq}` });
        return;
      }
      appendAuto(db, id, "handoff_requested", { to_provider: next });
      playProvider(db, id, next, AUTOMATION_ACTOR);
    }
  }

  /** The pre-approved result template goes out once the owner has accepted, if the patient's consent and contact details allow SMS. */
  function autoResultMessage(db: MockDb, id: string) {
    const evs = must(db, id);
    const consent = consentOf(evs);
    const genesis = evs[0]?.payload.contact_valid;
    if (consent.withdrawn || !consent.channel_sms) return;
    if (genesis === false || (genesis && typeof genesis === "object" && (genesis as Record<string, boolean>).sms === false)) return;
    db.notificationCounter += 1;
    appendAuto(db, id, "message_sent", {
      notification_id: `n-mock-${db.notificationCounter}`, channel: "sms", language: "en", purpose: "result_notification",
      template_id: "result_notification.sms", template_version: 1,
    });
  }

  /** The evidence scan found the colonoscopy: record the result (if booked), then propose closure. A clinician still signs off. */
  function autoEvidenceFound(db: MockDb, id: string, evidenceRef: string) {
    if (replayState(must(db, id)) === "confirmatory_booked") appendAuto(db, id, "complete_confirmatory", { result_ref: evidenceRef });
    const evs = must(db, id);
    if (replayState(evs) === "confirmatory_completed" && closureProposalOf(evs)?.evidence_ref !== evidenceRef) {
      appendAuto(db, id, "closure_proposed", { evidence_ref: evidenceRef });
    }
  }

  return {
    adapter: "mock",
    automation,
    async health() {
      return { status: "ok", sim_time: SIM_NOW };
    },
    async login(role, name, provider) {
      session = { role, name, provider: provider ?? ROLE_PROVIDERS[role] };
    },
    async actorFor(role, name) {
      return backendActor(role, name);
    },
    async recordConfirmatoryResult(id): Promise<ConfirmatoryResult> {
      const db = load();
      const evs = must(db, id);
      if (!OWNER_SCOPED_ROLES.includes(session.role)) throw new ApiError(403, `role '${BACKEND_ROLE[session.role]}' may not call POST /cases/{case_id}/confirmatory-result`);
      const owner = replayHandoffs(evs).owner;
      if (!owner || owner.actor !== actor()) throw new ApiError(403, `${BACKEND_ROLE[session.role]} '${session.name}' may only act on cases they own (this case's owner is ${owner?.name ?? "not yet assigned"})`);
      const evidenceRef = `Procedure/colonoscopy-${id}`;
      if (automation) {
        autoEvidenceFound(db, id, evidenceRef);
        save(db);
      }
      return {
        case_id: id, evidence_ref: evidenceRef, procedure_ref: evidenceRef,
        report_ref: `DiagnosticReport/colonoscopy-report-${id}`, recorded_at: SIM_NOW,
      };
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
      return caseFor(load(), id);
    },
    async getTimeline(id): Promise<CaseTimeline> {
      const db = load();
      const evs = must(db, id);
      // SEC-2: a patient's timeline is the visible events only, nothing derived (no SLA clocks, breaches, rungs, flags).
      if (session.role === "patient") return { case_id: id, sim_time: SIM_NOW, items: patientEvents(evs).map((e) => ({ kind: "event" as const, ...e })) };
      const items: TimelineItem[] = evs.map((e) => ({ kind: "event" as const, ...withKind(e) }));
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
      for (const [name, m] of Object.entries(milestonesOf(evs, SIM_NOW))) {
        if (m.started_at) items.push({ kind: "milestone_started", at: m.started_at, milestone: name, due_at: m.due_at, working_days: m.working_days });
        if (m.overdue && m.due_at) items.push({ kind: "milestone_breached", at: m.due_at, milestone: name, due_at: m.due_at });
        if (m.met_at) items.push({ kind: "milestone_met", at: m.met_at, milestone: name, due_at: m.due_at, late: m.met_late === true });
      }
      items.sort((a, b) => a.at.localeCompare(b.at));
      return { case_id: id, sim_time: SIM_NOW, items };
    },
    async postEvent(id, body: PostEventBody) {
      const db = load();
      const payload = body.payload ?? {};
      const stateBefore = replayState(must(db, id));
      append(db, id, body.type, body.actor, payload, { eventId: body.event_id });
      // The mock plays the receiving provider of a handoff (only the owner can request one, as in the real API).
      if (body.type === "handoff_requested") playProvider(db, id, String(payload.to_provider ?? ""), body.actor);
      if (automation) {
        if (body.type === "handoff_requested" || body.type === "handoff_rejected") autoReroute(db, id);
        if (body.type === "accept_ownership" && stateBefore === "acknowledged") autoResultMessage(db, id);
      }
      // Withdrawing consent puts the case on the human-review list (the API emits review_requested as system:consent).
      if (body.type === "consent_updated" && body.payload?.withdrawn === true) {
        const evs = must(db, id);
        append(db, id, "review_requested", "system:consent", { reason: "consent_withdrawn", source_event_id: evs.at(-1)?.event_id ?? `seq-${evs.length}` }, { internal: true });
      }
      save(db);
      return caseFor(db, id);
    },
    async requestDraft(id, body: DraftRequestBody): Promise<Draft> {
      requireStaff();
      const db = load();
      const evs = must(db, id);
      requireOwnerScope(db, id); // G-1: a clinician drafts only on a case they own, checked before the 409 states
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
      requireOwnerScope(db, id); // SEC-2: GET /cases/{id}/drafts is owner-scoped; a non-owner clinician gets 403
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
    async rejectDraft(draftId, body): Promise<DraftRejection> {
      const db = load();
      const d = db.drafts[draftId];
      if (!d) throw new ApiError(404, "draft not found");
      authorizeDecision(db, d.case_id, "reject");
      // Mirrors RejectDraft in api/app/routers/drafts.py (A16): a fixed taxonomy, a note only with other_with_note.
      if (!(REJECT_REASONS as readonly string[]).includes(body?.reason)) {
        throw new ApiError(422, `reason must be one of ${JSON.stringify(REJECT_REASONS)}`);
      }
      const note = (body.note ?? "").trim() || null;
      if (note && note.length > REJECT_NOTE_MAX) throw new ApiError(422, `note must be at most ${REJECT_NOTE_MAX} characters`);
      if (body.reason === "other_with_note" && !note) throw new ApiError(422, "reason other_with_note needs a note");
      if (body.reason !== "other_with_note" && note) throw new ApiError(422, "a note is only taken with reason other_with_note");
      if (d.approved_at) throw new ApiError(409, "draft is already approved");
      if (d.rejected_at) throw new ApiError(409, "draft is already rejected");
      const who = actor();
      // One step, like the API: message_rejected {draft_id, reason} in the log (the note stays on the draft row) and the decision on the draft.
      append(db, d.case_id, "message_rejected", who, { draft_id: draftId, reason: body.reason }, { internal: true });
      d.rejected_at = must(db, d.case_id).at(-1)!.at;
      d.rejected_by = who;
      d.rejection_reason = note ? `${body.reason}: ${note}` : body.reason;
      save(db);
      return { draft_id: draftId, rejected_by: who, rejected_at: d.rejected_at, reason: body.reason, note };
    },
    async closureMetrics(): Promise<ClosureMetrics> {
      return { ...SEED_METRICS, sim_time: SIM_NOW };
    },
    async getEffortMetrics(): Promise<EffortMetrics | null> {
      if (session.role !== "coordinator" && session.role !== "supervisor") return null; // the API answers 403
      return effortMetricsOf(load().events, SIM_NOW);
    },
    async createCase(): Promise<string> {
      requireStaff();
      const db = load();
      let n = Object.keys(db.events).length + 1;
      while (db.events[caseIdFor(n)]) n += 1;
      const id = caseIdFor(n);
      db.events[id] = [{ seq: 1, type: "case_created", at: nextAt(db), actor: "system:intake", payload: { patient_ref: `synthetic-patient-${id.slice(-3)}` } }];
      if (automation) {
        const who = ROLE_NAMES[ASSIGNED_OWNER.role];
        appendAuto(db, id, "acknowledge", {});
        appendAuto(db, id, "assign_ownership", { owner: backendActor(ASSIGNED_OWNER.role, who), provider: ASSIGNED_OWNER.provider });
      }
      save(db);
      return id;
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
