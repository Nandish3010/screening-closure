/**
 * Mock of the A22 agency layer: an in-memory task ledger seeded from the mock cases (see contract.ts, "Agency layer").
 *
 * The mock plays the whole agency deterministically: a Supervisor dispatches to five department heads (one dispatch
 * row each) and each head turns the dispatch into executioner tasks derived from the state of the cases. Agents are
 * labour, not authority: nothing here changes a case. A task that needs a decision is `needs_human`; it stays in the
 * inbox until the case moves on (a person acted on the case page) or the draft it prepared has been decided.
 * The ledger is plain JSON so it persists with the rest of the mock database.
 */
import type {
  AgencyDepartment, AgencyDepartmentNode, AgencyInboxItem, AgencyOrg, AgencyProvenance, AgencyRun, AgencyTask, AgencyTaskStatus,
  CaseAgencySummary, CaseEvent, CaseSummary, DraftRecord, StatusCounts,
} from "./contract";
import { AGENCY_DEPARTMENTS, AGENCY_DEPARTMENT_LABEL, PROVIDERS } from "./contract";
import { SIM_NOW } from "./fixtures";
import { actorRole, overdueDays } from "../lib/derive";

/** A stable 64-hex digest of the prompt inputs (a stand-in for the backend's SHA-256 of system + user prompt). */
export function fakePromptHash(input: string): string {
  let out = "";
  for (let round = 0; out.length < 64; round++) {
    let h = 0x811c9dc5 ^ (round * 0x9e3779b1);
    for (let i = 0; i < input.length; i++) h = Math.imul(h ^ input.charCodeAt(i), 0x01000193) >>> 0;
    out += h.toString(16).padStart(8, "0");
  }
  return out.slice(0, 64);
}

export const AGENCY_PRINCIPLE =
  "Agents prepare work; people decide. No agent can close a case, accept an escalation, approve a message or sign anything. Every agent acts through the same API, policy and approval gate as a person, and its work is recorded in the task ledger.";

interface ExecutionerSpec {
  agent_type: string;
  label: string;
  tools: string[];
  output_schema: string;
  replaces: string[];
}
interface DepartmentSpec {
  head_tools: string[];
  capabilities: string[];
  executioners: ExecutionerSpec[];
}

const SPEC: Record<AgencyDepartment, DepartmentSpec> = {
  // Mirrors the backend's allow-lists exactly (GET /agency/org; api/app/agency/agents.py). `capabilities` are the
  // events a department may cause through another actor's path: only Outreach, a draft through the drafts route.
  outreach: {
    head_tools: ["read_ledger"],
    capabilities: ["message_drafted"],
    executioners: [
      { agent_type: "draft_message", label: "Draft a patient message for approval", tools: ["read_case", "read_drafts", "create_draft"], output_schema: "DraftMessageOut", replaces: ["write a reminder or first message from scratch", "pick the channel and language the patient can receive"] },
      { agent_type: "followup_call_prep", label: "Prepare a follow-up call", tools: ["read_case", "read_notifications"], output_schema: "CallPrepOut", replaces: ["look up the contact history before calling", "write a call script that discloses no result"] },
    ],
  },
  referral_scheduling: {
    head_tools: ["read_ledger"],
    capabilities: [],
    executioners: [
      { agent_type: "reroute_referral", label: "Recommend where to re-route a rejected referral", tools: ["read_case", "read_providers"], output_schema: "RerouteOut", replaces: ["work out which provider has not yet been asked", "prepare the re-route for the coordinator"] },
      { agent_type: "prepare_handoff_pack", label: "Prepare the referral pack", tools: ["read_case", "read_providers"], output_schema: "HandoffPackOut", replaces: ["assemble the referral details and deadline", "check consent to share before referring"] },
      { agent_type: "booking_followup", label: "Follow up a booked colonoscopy", tools: ["read_case"], output_schema: "BookingOut", replaces: ["check whether a booked appointment has passed without a result"] },
    ],
  },
  evidence_closure: {
    head_tools: ["read_ledger"],
    capabilities: [],
    executioners: [
      { agent_type: "retrieve_evidence", label: "Look up the colonoscopy evidence in FHIR", tools: ["read_case", "find_evidence"], output_schema: "EvidenceOut", replaces: ["search FHIR for the colonoscopy report before closure"] },
      { agent_type: "prepare_closure_pack", label: "Prepare the closure pack for the signing clinician", tools: ["read_case", "find_evidence"], output_schema: "ClosurePackOut", replaces: ["assemble the case history, milestones and evidence before signing"] },
      { agent_type: "flag_missing_evidence", label: "Flag missing closure evidence", tools: ["read_case", "find_evidence"], output_schema: "EvidenceOut", replaces: ["notice that a closure has no resolving evidence"] },
    ],
  },
  patient_support: {
    head_tools: ["read_ledger"],
    capabilities: [],
    executioners: [
      { agent_type: "triage_reply", label: "Put a patient reply in front of a person", tools: ["read_case"], output_schema: "TriageOut", replaces: ["notice every incoming reply and queue it for a person, with the classifier's labels when it has them"] },
      { agent_type: "callback_brief", label: "Brief a call-back", tools: ["read_case", "read_notifications"], output_schema: "CallbackOut", replaces: ["gather the contact history before calling a patient back"] },
    ],
  },
  quality_ops: {
    head_tools: ["read_ledger"],
    capabilities: [],
    executioners: [
      { agent_type: "daily_summary", label: "Daily operations summary", tools: ["read_worklist", "read_metrics", "read_alerts", "read_ledger"], output_schema: "DailySummaryOut", replaces: ["compile the daily status report"] },
      { agent_type: "anomaly_check", label: "Operations anomaly check", tools: ["read_metrics", "read_alerts", "read_ledger"], output_schema: "FlagsOut", replaces: ["scan alerts, replay failures and unsafe closures each morning"] },
      { agent_type: "fairness_watch", label: "Fairness watch by language and channel", tools: ["read_abstention", "read_worklist"], output_schema: "FlagsOut", replaces: ["check that no language or channel group is served worse"] },
      { agent_type: "sla_risk_explainer", label: "Explain an SLA breach in one sentence", tools: ["read_case", "explain_factors"], output_schema: "ExplainOut", replaces: ["explain to a colleague why a case is at the top of the worklist"] },
    ],
  },
};

const actorOf = (d: AgencyDepartment | "supervisor") => `system:agency:${d}`;

/** Prose the mock keeps next to each task (the real API builds it per agent type). */
interface Meta {
  title: string;
  summary: string;
  recommended_action: string;
  material: Record<string, unknown>;
  assignee_role: string;
  links: { draft_id?: string };
}

export interface AgencyLedger {
  runs: AgencyRun[];
  tasks: AgencyTask[];
  meta: Record<string, Meta>;
  /** Next task number. */
  seq: number;
}

export interface CaseFact {
  summary: CaseSummary;
  events: CaseEvent[];
}

const zeroCounts = (): StatusCounts => ({ queued: 0, running: 0, done: 0, failed: 0, needs_human: 0 });
function countsOf(tasks: AgencyTask[]): StatusCounts {
  const c = zeroCounts();
  for (const t of tasks) c[t.status] += 1;
  return c;
}
const iso = (base: string, seconds: number) => new Date(new Date(base).getTime() + seconds * 1000).toISOString();
const short = (id: string) => id.slice(0, 8);
const NO_PROVENANCE: AgencyProvenance = { prompt_hash: null, provider: null, model_id: null, fallback_used: null };

export function emptyLedger(): AgencyLedger {
  return { runs: [], tasks: [], meta: {}, seq: 0 };
}

// ---------------------------------------------------------------------------------------------------------------------
// Planning: what the department heads would ask their executioners to do, from the state of the cases.
// ---------------------------------------------------------------------------------------------------------------------

interface Planned {
  department: AgencyDepartment;
  agent_type: string;
  case_id: string | null;
  status: AgencyTaskStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  provenance: AgencyProvenance;
  meta: Meta;
}

function lastPayload(events: CaseEvent[], type: string): Record<string, unknown> | undefined {
  return [...events].reverse().find((e) => e.type === type)?.payload;
}

function plan(facts: CaseFact[], drafts: Record<string, DraftRecord>, simAt: string): Planned[] {
  const out: Planned[] = [];
  const open = facts.filter((f) => f.summary.state !== "closed");
  const pendingDraftOf = (caseId: string) =>
    Object.values(drafts)
      .filter((d) => d.case_id === caseId && !d.approved_at && !d.rejected_at)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

  for (const f of open) {
    const s = f.summary;
    const id = s.case_id;
    const state = String(s.state);
    const ownerRole = s.owner_actor ? actorRole(s.owner_actor) : "coordinator";
    const base = (department: AgencyDepartment, agent_type: string) => ({ department, agent_type, case_id: id, input: { case_id: id, state } });
    const noMeta = (title: string, summary: string): Meta => ({ title, summary, recommended_action: "", material: {}, assignee_role: ownerRole, links: {} });

    if (state === "clinician_reviewed") {
      const evidence = lastPayload(f.events, "complete_confirmatory")?.result_ref ?? null;
      const signedBy = lastPayload(f.events, "clinician_review")?.signed_by ?? null;
      out.push({
        ...base("evidence_closure", "prepare_closure_pack"), status: "needs_human", provenance: NO_PROVENANCE,
        output: { evidence_ref: evidence, signed_by: signedBy, checklist: ["Evidence attached", "Clinician review signed", "Patient informed"] },
        meta: {
          title: `Closure pack ready for sign-off: case ${short(id)}`,
          summary: "Evidence and the clinician review are on file. The case waits for a signed closure.",
          recommended_action: "Open the case, check the evidence, then sign off and close it yourself.",
          material: { evidence_ref: evidence, signed_by: signedBy, checklist: ["Evidence attached", "Clinician review signed", "Patient informed"] },
          assignee_role: ownerRole, links: {},
        },
      });
    }
    if (state === "confirmatory_completed") {
      const evidence = lastPayload(f.events, "complete_confirmatory")?.result_ref;
      if (evidence) {
        out.push({
          ...base("evidence_closure", "retrieve_evidence"), status: "done", provenance: NO_PROVENANCE,
          output: { evidence_ref: evidence, source: "complete_confirmatory.result_ref" },
          meta: noMeta(`Evidence found: case ${short(id)}`, `The confirmatory result ${String(evidence)} is on file.`),
        });
      } else {
        out.push({
          ...base("evidence_closure", "flag_missing_evidence"), status: "needs_human", provenance: NO_PROVENANCE,
          output: { missing: ["confirmatory result reference"] },
          meta: {
            title: `Confirmatory result missing: case ${short(id)}`, summary: "The confirmatory test is marked complete but no result reference is on file.",
            recommended_action: "Ask the specialist to record the result, then continue from the case page.", material: { missing: ["confirmatory result reference"] },
            assignee_role: ownerRole, links: {},
          },
        });
      }
    }
    if (state === "referral_rejected") {
      const rejectedBy = s.last_handoff?.to_provider ?? null;
      const candidates = PROVIDERS.filter((p) => p !== rejectedBy);
      out.push({
        ...base("referral_scheduling", "reroute_referral"), status: "needs_human", provenance: NO_PROVENANCE,
        output: { rejected_by: rejectedBy, candidates: [...candidates], reason: "no response" },
        meta: {
          title: `Referral needs a new provider: case ${short(id)}`,
          summary: `${rejectedBy ?? "The receiving provider"} did not answer, so the referral bounced back. It must be re-routed before it escalates.`,
          recommended_action: "Open the case and reassign or hand off to one of the candidate providers.",
          material: { rejected_by: rejectedBy, candidate_providers: [...candidates], reason: "no response" },
          assignee_role: "coordinator", links: {},
        },
      });
    }
    if (s.pending_handoff && state !== "referral_rejected") {
      out.push({
        ...base("referral_scheduling", "prepare_handoff_pack"), status: "done", provenance: NO_PROVENANCE,
        output: { items: ["Result summary", "Consent status", "Contact details"], missing: [] },
        meta: noMeta(`Hand-off pack ready: case ${short(id)}`, `A pack for ${s.pending_handoff.to_provider} is assembled; the provider has not answered yet.`),
      });
    }
    if (state === "confirmatory_booked") {
      out.push({
        ...base("referral_scheduling", "booking_followup"), status: "failed", provenance: NO_PROVENANCE,
        output: { error: "calendar_tool_unavailable" },
        meta: noMeta(`Booking check failed: case ${short(id)}`, "The booking follow-up could not reach the calendar tool. It will be retried on the next run."),
      });
    }
    if (state === "patient_contacted") {
      const draft = pendingDraftOf(id);
      if (draft) {
        out.push({
          ...base("outreach", "draft_message"), status: "needs_human",
          provenance: { prompt_hash: draft.prompt_hash, provider: draft.provider, model_id: draft.model_id ?? null, fallback_used: draft.fallback_used },
          input: { case_id: id, state, draft_id: draft.draft_id },
          output: { draft_id: draft.draft_id, purpose: draft.purpose, channel: draft.channel, language: draft.language },
          meta: {
            title: `Reminder draft waiting for approval: case ${short(id)}`,
            summary: "A reminder is drafted for the patient. Nothing is sent until the case owner approves it.",
            recommended_action: "Open the case and approve or reject the draft in the drafts panel.",
            material: { draft_id: draft.draft_id, purpose: draft.purpose, channel: draft.channel, language: draft.language, written_by: draft.fallback_used ? "template (model unavailable)" : draft.provider },
            assignee_role: ownerRole, links: { draft_id: draft.draft_id },
          },
        });
      } else {
        out.push({
          ...base("outreach", "followup_call_prep"), status: "done", provenance: NO_PROVENANCE,
          output: { talking_points: ["Confirm the patient got the message", "Offer to book the next step"], channel: "phone" },
          meta: noMeta(`Call brief ready: case ${short(id)}`, "The patient was contacted and has not acknowledged yet. A short call brief is prepared."),
        });
      }
    }
    if (state === "owner_accepted" && s.overdue) {
      out.push({
        ...base("outreach", "followup_call_prep"), status: "needs_human", provenance: NO_PROVENANCE,
        output: { talking_points: ["Introduce the care team", "Explain the next step in plain words", "Ask for a good time to talk"], channel: "phone" },
        meta: {
          title: `Patient not yet contacted, call brief ready: case ${short(id)}`,
          summary: "The contact deadline has passed. A call brief is prepared for the person who rings the patient.",
          recommended_action: "Phone the patient, then record the contact on the case page.",
          material: { talking_points: ["Introduce the care team", "Explain the next step in plain words", "Ask for a good time to talk"], channel: "phone" },
          assignee_role: ownerRole, links: {},
        },
      });
    }
    if (state === "owner_accepted" && !s.overdue && s.risk_score !== undefined && s.risk_score >= 0.8) {
      out.push({
        ...base("outreach", "draft_message"), status: "queued", provenance: NO_PROVENANCE, output: null,
        meta: noMeta(`Message draft queued: case ${short(id)}`, "A high-risk case is waiting for a first message draft."),
      });
    }
    if (state === "patient_acknowledged") {
      out.push({
        ...base("patient_support", "triage_reply"), status: "done",
        provenance: { prompt_hash: fakePromptHash(`triage|${id}`), provider: "template", model_id: null, fallback_used: true },
        output: { category: "acknowledgement", reply_needed: false },
        meta: noMeta(`Patient reply triaged: case ${short(id)}`, "The patient acknowledged the result. No reply is needed."),
      });
    }
    if (s.consent_frozen) {
      out.push({
        ...base("patient_support", "callback_brief"), status: "needs_human", provenance: NO_PROVENANCE,
        output: { reason: "consent withdrawn", do_not: ["send messages", "start a new hand-off"] },
        meta: {
          title: `Consent withdrawn, review needed: case ${short(id)}`,
          summary: "The patient withdrew consent. Outreach is frozen and the case is on the human review list.",
          recommended_action: "Review the case. Contact the patient only after consent is re-confirmed.",
          material: { reason: "consent withdrawn", do_not: ["send messages", "start a new hand-off"], consent_frozen: true },
          assignee_role: "coordinator", links: {},
        },
      });
    }
    if (s.overdue) {
      const days = overdueDays(s.due_at, simAt);
      const sentence = `Case ${short(id)} is ${days > 0 ? `${days} day${days === 1 ? "" : "s"}` : "now"} past its SLA at ${state.replace(/_/g, " ")} and is owned by ${s.owner ?? "nobody yet"}.`;
      out.push({
        ...base("quality_ops", "sla_risk_explainer"), status: "done", provenance: NO_PROVENANCE, output: { sentence },
        meta: noMeta(`SLA risk explained: case ${short(id)}`, sentence),
      });
    }
    if (s.watchdog_flagged) {
      out.push({
        ...base("quality_ops", "anomaly_check"), status: "done", provenance: NO_PROVENANCE,
        output: { items: [{ kind: "idle", idle_days: s.watchdog_flagged.idle_days ?? null }] },
        meta: noMeta(`Idle case noticed: case ${short(id)}`, `Nothing has happened on this case for ${s.watchdog_flagged.idle_days ?? "many"} days. The watchdog flag stands.`),
      });
    }
  }

  const overdue = open.filter((f) => f.summary.overdue).length;
  const summary = `${open.length} open cases, ${overdue} past their SLA.`;
  out.push({
    department: "quality_ops", agent_type: "daily_summary", case_id: null, status: "done", input: { sim_at: simAt },
    provenance: { prompt_hash: fakePromptHash(`daily_summary|${simAt}`), provider: "template", model_id: null, fallback_used: true },
    output: { summary, open: open.length, overdue },
    meta: { title: "Daily summary", summary, recommended_action: "", material: {}, assignee_role: "supervisor", links: {} },
  });
  out.push({
    department: "quality_ops", agent_type: "fairness_watch", case_id: null, status: "done", input: { sim_at: simAt }, provenance: NO_PROVENANCE,
    output: { finding: "No gap above the threshold between languages or providers." },
    meta: { title: "Fairness watch", summary: "No gap above the threshold between languages or providers.", recommended_action: "", material: {}, assignee_role: "supervisor", links: {} },
  });
  return out;
}

// ---------------------------------------------------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------------------------------------------------

/** Is this task still waiting on a person? A person acting on the case page (the state moved on, the draft was decided) resolves it. */
function resolution(t: AgencyTask, facts: Map<string, CaseFact>, drafts: Record<string, DraftRecord>): { resolved_at: string; reason: string } | null {
  if (t.status !== "needs_human" || !t.case_id) return null;
  const f = facts.get(t.case_id);
  if (!f) return null;
  const draftId = typeof t.input.draft_id === "string" ? t.input.draft_id : null;
  const d = draftId ? drafts[draftId] : undefined;
  if (d && (d.approved_at || d.rejected_at)) return { resolved_at: d.approved_at ?? d.rejected_at ?? SIM_NOW, reason: d.approved_at ? "draft_approved" : "draft_rejected" };
  if (String(f.summary.state) !== t.input.state) return { resolved_at: f.summary.updated_at, reason: "case_state_changed" };
  return null;
}

function projected(t: AgencyTask, facts: Map<string, CaseFact>, drafts: Record<string, DraftRecord>): AgencyTask {
  const r = resolution(t, facts, drafts);
  if (!r) return structuredClone(t);
  return { ...structuredClone(t), resolved_at: r.resolved_at, resolved_by: "human", resolution: { reason: r.reason } };
}

function summariseRun(run: AgencyRun, tasks: AgencyTask[]) {
  const mine = tasks.filter((t) => t.run_id === run.run_id);
  const by_department = Object.fromEntries(
    AGENCY_DEPARTMENTS.map((d) => {
      const own = mine.filter((t) => t.department === d);
      return [d, { ...countsOf(own), total: own.length }];
    }),
  ) as AgencyRun["counts"]["by_department"];
  run.counts = { total: mine.length, by_status: countsOf(mine), by_department };
}

const withCounts = (run: AgencyRun, ledger: AgencyLedger): AgencyRun => {
  const copy = structuredClone(run);
  summariseRun(copy, ledger.tasks);
  return copy;
};

/** Run the agency once at `simAt`: one supervisor task, five dispatch rows, then the executioner tasks. Mutates the ledger. */
export function executeRun(
  ledger: AgencyLedger, facts: CaseFact[], drafts: Record<string, DraftRecord>,
  opts: { simAt: string; trigger: "manual" | "scheduler"; createdBy: string },
): AgencyRun {
  const runId = `run-${String(ledger.runs.length + 1).padStart(4, "0")}`;
  const mintId = () => `task-${String(++ledger.seq).padStart(5, "0")}`;
  const factMap = new Map(facts.map((f) => [f.summary.case_id, f]));
  const stale = (t: AgencyTask) => resolution(t, factMap, drafts) !== null;
  let tick = 0;
  const push = (t: Omit<AgencyTask, "task_id" | "run_id" | "created_at" | "resolved_at" | "resolved_by" | "resolution" | "started_at" | "finished_at"> & { meta: Meta }) => {
    const { meta, ...rest } = t;
    const started = rest.status === "queued" ? null : iso(opts.simAt, ++tick);
    const finished = rest.status === "queued" || rest.status === "running" ? null : iso(opts.simAt, ++tick);
    const task: AgencyTask = {
      ...rest, task_id: mintId(), run_id: runId, created_at: opts.simAt, started_at: started, finished_at: finished,
      resolved_at: null, resolved_by: null, resolution: null,
    };
    ledger.tasks.push(task);
    ledger.meta[task.task_id] = meta;
    return task;
  };
  const noMeta: Meta = { title: "", summary: "", recommended_action: "", material: {}, assignee_role: "supervisor", links: {} };

  const root = push({
    parent_id: null, department: "supervisor", agent_type: "supervisor", case_id: null, status: "done", input: { sim_at: opts.simAt },
    output: { dispatched: [...AGENCY_DEPARTMENTS] }, provenance: NO_PROVENANCE, created_by: actorOf("supervisor"),
    meta: { ...noMeta, title: "Supervisor plan", summary: "Dispatched work to the five departments." },
  });

  // An open task that already waits for a person is not asked for again.
  const alreadyOpen = (p: Planned) =>
    (p.status === "needs_human" || p.status === "queued") &&
    ledger.tasks.some((t) => t.run_id !== runId && t.agent_type === p.agent_type && t.case_id === p.case_id && (t.status === "needs_human" || t.status === "queued") && !stale(t));
  const planned = plan(facts, drafts, opts.simAt).filter((p) => !alreadyOpen(p));

  const rationale: AgencyRun["rationale"] = {};
  for (const d of AGENCY_DEPARTMENTS) {
    const mine = planned.filter((p) => p.department === d);
    const needing = mine.filter((p) => p.status === "needs_human").length;
    rationale[d] = mine.length === 0
      ? "No new work for this department at this sim time."
      : `${mine.length} task${mine.length === 1 ? "" : "s"} from the current state of the cases${needing ? `, ${needing} for a person to decide` : ""}.`;
    const head = push({
      parent_id: root.task_id, department: d, agent_type: "department_head", case_id: null, status: "done", input: { dispatch: d },
      output: { rationale: rationale[d], dispatched: mine.length }, provenance: NO_PROVENANCE, created_by: actorOf(d),
      meta: { ...noMeta, title: `${AGENCY_DEPARTMENT_LABEL[d]} dispatch`, summary: rationale[d]! },
    });
    for (const p of mine) {
      const { meta, ...rest } = p;
      push({ ...rest, parent_id: head.task_id, created_by: actorOf(d), meta });
    }
  }

  const run: AgencyRun = {
    run_id: runId, trigger: opts.trigger, sim_at: opts.simAt, created_by: opts.createdBy, status: "done",
    planner: { prompt_hash: fakePromptHash(`planner|${opts.simAt}`), provider: "template", model_id: null, fallback_used: true },
    started_at: iso(opts.simAt, 0), finished_at: iso(opts.simAt, tick + 1),
    counts: { total: 0, by_status: zeroCounts(), by_department: {} as AgencyRun["counts"]["by_department"] }, rationale,
  };
  ledger.runs.unshift(run);
  summariseRun(run, ledger.tasks);
  return run;
}

/** The ledger a fresh mock starts with: one earlier scheduler run, six sim-hours before "now". */
export function seedLedger(facts: CaseFact[], drafts: Record<string, DraftRecord>): AgencyLedger {
  const ledger = emptyLedger();
  executeRun(ledger, facts, drafts, { simAt: iso(SIM_NOW, -6 * 3600), trigger: "scheduler", createdBy: "system:scheduler" });
  return ledger;
}

// ---------------------------------------------------------------------------------------------------------------------
// Reads (the same shapes the endpoints answer)
// ---------------------------------------------------------------------------------------------------------------------

const factsMap = (facts: CaseFact[]) => new Map(facts.map((f) => [f.summary.case_id, f]));

export function orgOf(ledger: AgencyLedger): AgencyOrg {
  const departments: AgencyDepartmentNode[] = AGENCY_DEPARTMENTS.map((d) => {
    const own = ledger.tasks.filter((t) => t.department === d);
    return {
      department: d, label: AGENCY_DEPARTMENT_LABEL[d], actor: actorOf(d), head_tools: [...SPEC[d].head_tools], capabilities: [...SPEC[d].capabilities],
      executioners: SPEC[d].executioners.map((e) => ({ ...structuredClone(e), counts: countsOf(own.filter((t) => t.agent_type === e.agent_type)) })),
      counts: countsOf(own),
    };
  });
  return {
    supervisor: { actor: actorOf("supervisor"), agent_type: "supervisor", tools: ["read_active_cases", "read_metrics", "read_alerts", "read_ledger"] },
    departments,
    last_run: ledger.runs[0] ? withCounts(ledger.runs[0], ledger) : null,
    principle: AGENCY_PRINCIPLE,
  };
}

export function runsOf(ledger: AgencyLedger, limit?: number): AgencyRun[] {
  const runs = ledger.runs.map((r) => withCounts(r, ledger));
  return limit === undefined ? runs : runs.slice(0, Math.max(0, limit));
}

export function tasksOf(
  ledger: AgencyLedger, facts: CaseFact[], drafts: Record<string, DraftRecord>,
  p: { status?: string; department?: string; case_id?: string; run_id?: string; limit?: number } = {},
): AgencyTask[] {
  const fm = factsMap(facts);
  const rows = ledger.tasks
    .filter((t) => !p.status || t.status === p.status)
    .filter((t) => !p.department || t.department === p.department)
    .filter((t) => !p.case_id || t.case_id === p.case_id)
    .filter((t) => !p.run_id || t.run_id === p.run_id)
    .map((t) => projected(t, fm, drafts));
  return p.limit === undefined ? rows : rows.slice(0, Math.max(0, p.limit));
}

/** Items waiting for a person, oldest first. `ownerActor` limits them to the cases that owner holds (owner roles). */
export function inboxOf(ledger: AgencyLedger, facts: CaseFact[], drafts: Record<string, DraftRecord>, ownerActor?: string): AgencyInboxItem[] {
  const fm = factsMap(facts);
  const items: AgencyInboxItem[] = [];
  for (const t of ledger.tasks) {
    if (t.status !== "needs_human" || t.department === "supervisor") continue;
    if (resolution(t, fm, drafts)) continue;
    if (ownerActor !== undefined && (!t.case_id || fm.get(t.case_id)?.summary.owner_actor !== ownerActor)) continue;
    const m = ledger.meta[t.task_id];
    items.push({
      task_id: t.task_id, run_id: t.run_id, department: t.department, agent_type: t.agent_type, case_id: t.case_id, created_at: t.created_at,
      assignee_role: m.assignee_role, title: m.title, summary: m.summary, recommended_action: m.recommended_action,
      material: structuredClone(m.material), links: { ...m.links },
    });
  }
  return items;
}

export function caseSummaryOf(ledger: AgencyLedger, facts: CaseFact[], drafts: Record<string, DraftRecord>, caseId: string): CaseAgencySummary {
  const fm = factsMap(facts);
  const rows = ledger.tasks.filter((t) => t.case_id === caseId && t.department !== "supervisor");
  const waiting = (t: AgencyTask) => t.status === "needs_human" && !resolution(t, fm, drafts);
  return {
    case_id: caseId,
    total: rows.length,
    open: rows.filter((t) => waiting(t) || t.status === "queued" || t.status === "running").length,
    needs_human: rows.filter(waiting).length,
    tasks: rows.map((t) => ({
      task_id: t.task_id, department: t.department as AgencyDepartment, agent_type: t.agent_type, status: t.status, created_at: t.created_at,
      title: ledger.meta[t.task_id].title, summary: ledger.meta[t.task_id].summary,
    })),
  };
}
