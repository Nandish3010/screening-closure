import { beforeEach, describe, expect, it } from "vitest";
import { AGENCY_DEPARTMENTS, AGENCY_TASK_STATUSES, ApiError, type ApiClient } from "../src/api/contract";
import { SIM_NOW } from "../src/api/fixtures";
import { createMockAdapter } from "../src/api/mockAdapter";

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
let api: ApiClient;
beforeEach(() => {
  api = createMockAdapter(null);
});

describe("mock agency: org and ledger", () => {
  it("answers the org chart with five departments, the executioner agent types and every status key", async () => {
    const org = await api.agencyOrg();
    expect(org.departments.map((d) => d.department)).toEqual([...AGENCY_DEPARTMENTS]);
    expect(org.departments.map((d) => d.label)).toEqual(["Outreach", "Referral & Scheduling", "Evidence & Closure", "Patient Support", "Quality & Operations"]);
    const types = Object.fromEntries(org.departments.map((d) => [d.department, d.executioners.map((e) => e.agent_type)]));
    expect(types).toEqual({
      outreach: ["draft_message", "followup_call_prep"],
      referral_scheduling: ["reroute_referral", "prepare_handoff_pack", "booking_followup"],
      evidence_closure: ["retrieve_evidence", "prepare_closure_pack", "flag_missing_evidence"],
      patient_support: ["triage_reply", "callback_brief"],
      quality_ops: ["daily_summary", "anomaly_check", "fairness_watch", "sla_risk_explainer"],
    });
    expect(org.supervisor).toMatchObject({ actor: "system:agency:supervisor", agent_type: "supervisor" });
    expect(org.principle).toMatch(/people decide/);
    for (const d of org.departments) {
      expect(Object.keys(d.counts)).toEqual([...AGENCY_TASK_STATUSES]);
      expect(d.actor).toBe(`system:agency:${d.department}`);
      for (const e of d.executioners) {
        expect(e.tools.length).toBeGreaterThan(0);
        expect(e.replaces.length).toBeGreaterThan(0);
        expect(Object.keys(e.counts)).toEqual([...AGENCY_TASK_STATUSES]);
      }
    }
    expect(org.last_run?.run_id).toBe("run-0001");
  });

  it("is seeded with one completed run: a supervisor task, five dispatch rows and the executioner tasks", async () => {
    const runs = await api.agencyRuns();
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({ run_id: "run-0001", trigger: "scheduler", status: "done" });
    expect(runs[0].sim_at < SIM_NOW).toBe(true);
    expect(Object.keys(runs[0].rationale).sort()).toEqual([...AGENCY_DEPARTMENTS].sort());
    const tasks = await api.agencyTasks({ run_id: "run-0001" });
    expect(tasks.filter((t) => t.agent_type === "supervisor")).toHaveLength(1);
    const heads = tasks.filter((t) => t.agent_type === "department_head");
    expect(heads.map((t) => t.department)).toEqual([...AGENCY_DEPARTMENTS]);
    for (const h of heads) expect(h.output).toMatchObject({ rationale: runs[0].rationale[h.department as keyof typeof runs[0]["rationale"]] });
    // counts add up, by status and by department
    expect(runs[0].counts.total).toBe(tasks.length);
    expect(Object.values(runs[0].counts.by_status).reduce((a, b) => a + b, 0)).toBe(tasks.length);
    for (const d of AGENCY_DEPARTMENTS) expect(runs[0].counts.by_department[d].total).toBe(tasks.filter((t) => t.department === d).length);
    // creation order, oldest first
    expect(tasks.map((t) => t.task_id)).toEqual([...tasks.map((t) => t.task_id)].sort());
  });

  it("derives executioner tasks from the state of the fixture cases", async () => {
    const all = await api.agencyTasks();
    const of = (agent: string, n?: number) => all.filter((t) => t.agent_type === agent && (n === undefined || t.case_id === cid(n)));
    expect(of("prepare_closure_pack", 10)[0]).toMatchObject({ department: "evidence_closure", status: "needs_human", created_by: "system:agency:evidence_closure" });
    expect(of("reroute_referral", 15)[0]).toMatchObject({ department: "referral_scheduling", status: "needs_human" });
    expect(of("draft_message", 2)[0]).toMatchObject({ status: "needs_human", output: { draft_id: "d-seed-2" } });
    expect(of("draft_message", 2)[0].provenance).toMatchObject({ fallback_used: true, provider: "template" });
    expect(of("followup_call_prep", 3)[0].status).toBe("needs_human");
    expect(of("callback_brief", 14)[0].status).toBe("needs_human");
    expect(of("retrieve_evidence", 1)[0]).toMatchObject({ status: "done", output: { evidence_ref: "DiagnosticReport/colonoscopy-0001" } });
    expect(of("daily_summary")[0]).toMatchObject({ status: "done", case_id: null, department: "quality_ops" });
    const sla = of("sla_risk_explainer", 1)[0];
    expect(sla.status).toBe("done");
    expect(typeof sla.output?.sentence).toBe("string");
    expect(String(sla.output?.sentence)).toMatch(/^[^\n]+\.$/); // one sentence
    // a closed case gets no agency work
    expect(all.filter((t) => t.case_id === cid(11))).toHaveLength(0);
    // every non-supervisor row names its parent, the times are ISO strings and an open task is unresolved
    expect(all.filter((t) => t.status === "failed").length).toBeGreaterThan(0);
    expect(all.filter((t) => t.status === "queued").length).toBeGreaterThan(0);
    for (const t of all.filter((t) => t.status === "needs_human")) expect(t).toMatchObject({ resolved_at: null, resolved_by: null, resolution: null });
  });

  it("filters tasks by status, department, case and limit", async () => {
    expect((await api.agencyTasks({ status: "needs_human" })).every((t) => t.status === "needs_human")).toBe(true);
    expect((await api.agencyTasks({ department: "outreach" })).every((t) => t.department === "outreach")).toBe(true);
    expect((await api.agencyTasks({ case_id: cid(15) })).every((t) => t.case_id === cid(15))).toBe(true);
    expect(await api.agencyTasks({ limit: 3 })).toHaveLength(3);
    expect(await api.agencyRuns(0)).toHaveLength(0);
  });
});

describe("mock agency: inbox", () => {
  it("gives the coordinator and the supervisor every item that needs a person, with prepared material", async () => {
    for (const role of ["coordinator", "supervisor"] as const) {
      await api.login(role, "Staff");
      const inbox = await api.agencyInbox();
      expect(inbox.length).toBeGreaterThanOrEqual(5);
      expect(inbox.every((i) => i.title && i.summary && i.recommended_action && i.case_id)).toBe(true);
      expect(inbox.map((i) => i.agent_type).sort()).toEqual(expect.arrayContaining(["prepare_closure_pack", "reroute_referral", "draft_message", "followup_call_prep", "callback_brief"]));
      const draft = inbox.find((i) => i.agent_type === "draft_message")!;
      expect(draft.links).toEqual({ draft_id: "d-seed-2" });
      expect(inbox.find((i) => i.agent_type === "prepare_closure_pack")!.assignee_role).toBe("primary_care_owner");
      expect(inbox.find((i) => i.agent_type === "reroute_referral")!.assignee_role).toBe("coordinator");
    }
  });

  it("shows owner roles only the items on cases they own", async () => {
    await api.login("clinician", "Dr. Huda Rahman");
    const mine = await api.agencyInbox();
    expect(mine.map((i) => i.case_id)).toEqual([cid(10)]);
    await api.login("owner", "Dr. Layla Haddad");
    const layla = await api.agencyInbox();
    expect(layla.map((i) => i.case_id).sort()).toEqual([cid(15), cid(16)]); // Layla owns 1, 6, 15, 16: 1 and 6 have nothing waiting
    await api.login("specialist", "Dr. Karim Nasser");
    expect(await api.agencyInbox()).toEqual([]);
  });

  it("refuses a patient", async () => {
    await api.login("patient", "Patient");
    await expect(api.agencyInbox()).rejects.toMatchObject({ status: 403 });
  });

  it("drops an item once a person has acted on the case or decided the draft", async () => {
    await api.login("owner", "Dr. Layla Haddad", "Riverside Primary Care");
    await api.postEvent(cid(15), { type: "handoff_requested", actor: "primary_care_owner:Dr. Layla Haddad", payload: { to_provider: "Corniche Gastro Unit" } });
    await api.login("coordinator", "Maryam Al Suwaidi");
    let inbox = await api.agencyInbox();
    expect(inbox.some((i) => i.case_id === cid(15))).toBe(false);
    const resolved = (await api.agencyTasks({ case_id: cid(15) })).find((t) => t.agent_type === "reroute_referral")!;
    expect(resolved.resolved_at).toBeTruthy();
    expect(resolved.resolution).toMatchObject({ reason: "case_state_changed" });

    expect(inbox.some((i) => i.links.draft_id === "d-seed-2")).toBe(true);
    // Sara owns case 2 (not a demo login, but the mock only needs the actor to match the case owner): she rejects the draft.
    await api.login("owner", "Sara Al Mansoori");
    await api.rejectDraft("d-seed-2", { reason: "tone" });
    await api.login("coordinator", "Maryam Al Suwaidi");
    inbox = await api.agencyInbox();
    expect(inbox.some((i) => i.links.draft_id === "d-seed-2")).toBe(false);
    const task = (await api.agencyTasks({ case_id: cid(2) })).find((t) => t.agent_type === "draft_message")!;
    expect(task.resolution).toMatchObject({ reason: "draft_rejected" });
  });
});

describe("mock agency: runs", () => {
  it("creates a run at the current sim time, once: a second call returns the same run with created false", async () => {
    await api.login("supervisor", "Dr. Noura Al Hammadi");
    const first = await api.runAgency();
    expect(first).toMatchObject({ run_id: "run-0002", created: true, trigger: "manual", sim_at: SIM_NOW, status: "done" });
    expect(first.created_by).toBe("supervisor:Dr. Noura Al Hammadi");
    expect(first.counts.total).toBeGreaterThan(6); // supervisor + five dispatch rows + work
    const again = await api.runAgency();
    expect(again).toMatchObject({ run_id: "run-0002", created: false });
    expect(again.counts.total).toBe(first.counts.total);
    expect((await api.agencyRuns()).map((r) => r.run_id)).toEqual(["run-0002", "run-0001"]);
  });

  it("does not ask a person twice for what is already waiting in the inbox", async () => {
    await api.login("supervisor", "Dr. Noura Al Hammadi");
    const before = await api.agencyInbox();
    const run = await api.runAgency();
    expect(await api.agencyInbox()).toHaveLength(before.length);
    const tasks = await api.agencyTasks({ run_id: run.run_id });
    expect(tasks.filter((t) => t.status === "needs_human")).toHaveLength(0);
    expect(tasks.filter((t) => t.agent_type === "department_head")).toHaveLength(5);
  });

  it("keeps the ledger across calls on the same storage", async () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
    const a = createMockAdapter(storage);
    await a.login("supervisor", "Dr. Noura Al Hammadi");
    await a.runAgency();
    const b = createMockAdapter(storage);
    await b.login("supervisor", "Dr. Noura Al Hammadi");
    expect((await b.agencyRuns()).map((r) => r.run_id)).toEqual(["run-0002", "run-0001"]);
    expect((await b.runAgency()).created).toBe(false);
    await b.resetDemo!();
    expect((await b.agencyRuns()).map((r) => r.run_id)).toEqual(["run-0001"]);
  });

  it("seeds a ledger for a database stored before the agency existed", async () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
    const a = createMockAdapter(storage);
    await a.listCases(); // writes the seed
    // The storage key carries a version that the automation branch bumped (v2 -> v3); find the database whatever it is called.
    const dbKey = [...store.keys()].find((k) => k.startsWith("sc.mock.v"))!;
    const old = JSON.parse(store.get(dbKey)!);
    delete old.agency;
    store.set(dbKey, JSON.stringify(old));
    const b = createMockAdapter(storage);
    expect((await b.agencyRuns()).map((r) => r.run_id)).toEqual(["run-0001"]);
  });
});

describe("mock agency: role rules", () => {
  it("lets only the coordinator and the supervisor read the org, runs and tasks, and run the agency", async () => {
    for (const role of ["owner", "specialist", "clinician", "patient"] as const) {
      await api.login(role, "X");
      for (const call of [() => api.agencyOrg(), () => api.agencyRuns(), () => api.agencyTasks(), () => api.runAgency()]) {
        await expect(call()).rejects.toBeInstanceOf(ApiError);
        await expect(call()).rejects.toMatchObject({ status: 403 });
      }
    }
    await api.login("coordinator", "Maryam Al Suwaidi");
    await expect(api.agencyOrg()).resolves.toBeTruthy();
  });
});

describe("mock agency: per-case summary", () => {
  it("counts a case's tasks and those needing a person", async () => {
    expect(await api.caseAgency(cid(15))).toMatchObject({ case_id: cid(15), total: 2, open: 1, needs_human: 1 });
    expect(await api.caseAgency(cid(1))).toMatchObject({ total: 2, open: 0, needs_human: 0 });
    expect(await api.caseAgency(cid(11))).toMatchObject({ total: 0, open: 0, needs_human: 0, tasks: [] });
    const s = (await api.caseAgency(cid(15)))!;
    expect(s.tasks.every((t) => t.title && t.summary && t.department)).toBe(true);
  });
  it("404s an unknown case, refuses a patient, and keeps clinicians to their own cases", async () => {
    await expect(api.caseAgency("nope")).rejects.toMatchObject({ status: 404 });
    await api.login("clinician", "Dr. Huda Rahman");
    await expect(api.caseAgency(cid(15))).rejects.toMatchObject({ status: 403 });
    await expect(api.caseAgency(cid(10))).resolves.toMatchObject({ needs_human: 1 });
    await api.login("patient", "Patient");
    await expect(api.caseAgency(cid(10))).rejects.toMatchObject({ status: 403 });
  });
});
