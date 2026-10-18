import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type {
  AgencyDepartmentNode, AgencyInboxItem, AgencyOrg, AgencyProvenance, AgencyRun, AgencyTask, AgencyTaskStatus, Role, StatusCounts,
} from "../api/contract";
import { AGENCY_DEPARTMENTS, AGENCY_DEPARTMENT_LABEL, AGENCY_TASK_STATUSES, ApiError } from "../api/contract";
import { fmtDateTime, shortId, titleCase } from "../lib/format";

/** Shown when the API sends no `principle`. */
export const AGENCY_PRINCIPLE_FALLBACK =
  "Agents prepare work; people decide. No agent can close a case, accept an escalation, approve a message or sign anything. Every agent acts through the same API, policy and approval gate as a person, and its work is recorded in the task ledger.";

const STATUS_CHIP: Record<AgencyTaskStatus, string> = { queued: "", running: "", done: "chip-ok", failed: "chip-bad", needs_human: "chip-warn" };
const STATUS_LABEL: Record<AgencyTaskStatus, string> = { queued: "queued", running: "running", done: "done", failed: "failed", needs_human: "needs human" };

/** How many recent runs the timeline offers. */
const RUN_LIMIT = 10;

function problem(e: unknown): { kind: "unavailable" | "denied" | "error"; message: string } {
  if (e instanceof ApiError && e.status === 404) return { kind: "unavailable", message: "Agency is not available on this server yet." };
  if (e instanceof ApiError && e.status === 403) return { kind: "denied", message: "Your role cannot see this part of the agency." };
  return { kind: "error", message: String((e as Error)?.message ?? e) };
}

/** The five task counts of one node, as badges. A zero is shown plainly so the absence of work is visible too. */
export function StatusBadges({ counts, label }: { counts: StatusCounts; label: string }) {
  return (
    <ul className="plain agency-counts" aria-label={label}>
      {AGENCY_TASK_STATUSES.map((s) => (
        <li key={s} className={`chip ${counts[s] > 0 ? STATUS_CHIP[s] : ""}`} data-testid={`count-${s}`}>
          {STATUS_LABEL[s]} <strong>{counts[s] ?? 0}</strong>
        </li>
      ))}
    </ul>
  );
}

function Tools({ tools, label }: { tools: string[]; label: string }) {
  if (tools.length === 0) return <p className="muted agency-line">No tools.</p>;
  return (
    <ul className="plain agency-tools" aria-label={label}>
      {tools.map((t) => <li key={t} className="mono">{t}</li>)}
    </ul>
  );
}

function Department({ node }: { node: AgencyDepartmentNode }) {
  return (
    <section className="panel agency-dept" aria-label={`${node.label} department`} data-testid={`dept-${node.department}`}>
      <h3>{node.label}</h3>
      <p className="muted agency-line">Head agent <span className="mono">{node.actor}</span></p>
      <StatusBadges counts={node.counts} label={`${node.label} task counts`} />
      <Tools tools={node.head_tools} label={`${node.label} head tools`} />
      {node.capabilities.length > 0 && (
        <ul className="agency-capabilities" aria-label={`${node.label} capabilities`}>
          {node.capabilities.map((c) => <li key={c}>{c}</li>)}
        </ul>
      )}
      <h4>Agents</h4>
      <ul className="plain agency-agents">
        {node.executioners.map((e) => (
          <li key={e.agent_type} data-testid={`agent-${e.agent_type}`}>
            <p className="agency-agent-name"><strong>{e.label}</strong> <span className="mono">{e.agent_type}</span></p>
            <StatusBadges counts={e.counts} label={`${e.label} task counts`} />
            <Tools tools={e.tools} label={`${e.label} tools`} />
            {e.replaces.length > 0 && <p className="agency-line">Replaces: {e.replaces.join("; ")}.</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function OrgChart({ org }: { org: AgencyOrg }) {
  const byKey = new Map(org.departments.map((d) => [d.department, d]));
  const ordered = [...AGENCY_DEPARTMENTS.flatMap((k) => (byKey.has(k) ? [byKey.get(k)!] : [])), ...org.departments.filter((d) => !AGENCY_DEPARTMENTS.includes(d.department))];
  return (
    <section aria-labelledby="org-h" data-testid="agency-org">
      <h2 id="org-h">Organisation</h2>
      <section className="panel agency-supervisor" aria-label="Supervisor" data-testid="agency-supervisor">
        <h3>Supervisor</h3>
        <p className="muted agency-line">Plans the run and dispatches to the five departments. <span className="mono">{org.supervisor.actor}</span></p>
        <Tools tools={org.supervisor.tools} label="Supervisor tools" />
      </section>
      <p className="agency-arrow muted" aria-hidden="true">dispatches to</p>
      <ul className="plain agency-depts">
        {ordered.map((d) => <li key={d.department}><Department node={d} /></li>)}
      </ul>
    </section>
  );
}

/** Values of a task's prepared material: arrays joined, objects as compact JSON. */
function show(v: unknown): string {
  if (v === null || v === undefined) return "-";
  if (Array.isArray(v)) return v.map(show).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** A nested object (a closure pack, a history) is prepared material, not a headline: it stays folded until someone opens it. */
const isNested = (v: unknown) => v !== null && typeof v === "object" && !Array.isArray(v) && Object.keys(v as object).length > 3;

export function Material({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return null;
  return (
    <dl className="agency-material">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt>{titleCase(k)}</dt>
          <dd>
            {isNested(v)
              ? <details><summary>{Object.keys(v as object).length} fields (open to read)</summary><pre className="mono">{show(v)}</pre></details>
              : show(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Inbox({ items, error, loaded }: { items: AgencyInboxItem[]; error: string | null; loaded: boolean }) {
  return (
    <section className="panel" aria-labelledby="inbox-h" data-testid="agency-inbox-panel">
      <h2 id="inbox-h">Needs a person</h2>
      <p className="muted">Agents prepared these and stopped. Open the case and use its own controls to decide; nothing on this page changes a case.</p>
      {error && <p className="banner banner-bad" role="alert">Could not load the inbox: {error}</p>}
      {!loaded && !error && <p role="status">Loading the inbox...</p>}
      {loaded && !error && items.length === 0 && <p className="banner banner-ok" data-testid="inbox-empty">Nothing needs a person right now.</p>}
      {items.length > 0 && (
        <ul className="plain agency-inbox" data-testid="agency-inbox">
          {items.map((it) => (
            <li key={it.task_id} data-testid="inbox-item">
              <h3>{it.title}</h3>
              <p className="agency-meta">
                <span className="chip">{AGENCY_DEPARTMENT_LABEL[it.department] ?? it.department}</span>{" "}
                <span className="mono">{it.agent_type}</span>
                <span className="muted"> · for {titleCase(it.assignee_role)} · prepared <time dateTime={it.created_at}>{fmtDateTime(it.created_at)}</time></span>
              </p>
              <p>{it.summary}</p>
              <p><strong>Recommended:</strong> {it.recommended_action}</p>
              <Material data={{ ...it.material, ...(it.links.draft_id ? { draft: it.links.draft_id } : {}) }} />
              {it.case_id && (
                <p>
                  <Link to={`/coordinator/${it.case_id}`}>Open case<span className="visually-hidden"> {shortId(it.case_id)}</span></Link>
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Provenance({ p, testId }: { p: AgencyProvenance; testId: string }) {
  return (
    <dl className="agency-material" data-testid={testId}>
      <div><dt>Provider</dt><dd>{p.provider ?? "-"}</dd></div>
      <div><dt>Model</dt><dd>{p.model_id ?? "-"}</dd></div>
      <div><dt>Fallback used</dt><dd>{p.fallback_used === null ? "-" : p.fallback_used ? "yes (template instead of a model)" : "no"}</dd></div>
    </dl>
  );
}

function TaskRow({ t, run }: { t: AgencyTask; run: AgencyRun }) {
  const dispatch = t.agent_type === "department_head";
  const dept = t.department === "supervisor" ? null : t.department;
  const why = dispatch ? (dept ? run.rationale[dept] : undefined) ?? (typeof t.output?.rationale === "string" ? t.output.rationale : undefined) : undefined;
  return (
    <li data-testid="run-task" data-agent-type={t.agent_type}>
      <span className="mono">{t.agent_type}</span>{" "}
      <span className={`chip ${STATUS_CHIP[t.status]}`}>{STATUS_LABEL[t.status]}</span>
      {t.resolved_at && <> <span className="chip chip-ok">resolved by a person</span></>}
      {t.case_id && <> <Link to={`/coordinator/${t.case_id}`}>Case {shortId(t.case_id)}</Link></>}
      <span className="muted"> · started {fmtDateTime(t.started_at)} · finished {fmtDateTime(t.finished_at)}</span>
      {why && <p className="agency-why" data-testid="dispatch-rationale">{why}</p>}
    </li>
  );
}

function RunTimeline({ runs, runId, onSelect, tasks, tasksError }: {
  runs: AgencyRun[]; runId: string | null; onSelect: (id: string) => void; tasks: AgencyTask[] | null; tasksError: string | null;
}) {
  const run = runs.find((r) => r.run_id === runId) ?? null;
  const groups: (AgencyTask["department"])[] = ["supervisor", ...AGENCY_DEPARTMENTS];
  return (
    <>
      {runs.length === 0 && <p className="muted" data-testid="runs-empty">No run yet.</p>}
      {runs.length > 0 && (
        <label className="field">
          <span>Run</span>
          <select value={runId ?? ""} onChange={(e) => onSelect(e.target.value)} aria-label="Select run" data-testid="run-select">
            {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id} · {fmtDateTime(r.sim_at)} · {r.status}</option>)}
          </select>
        </label>
      )}
      {run && (
        <div data-testid="run-detail">
          {run.status === "budget_exhausted" && (
            <p className="banner banner-warn" role="status" data-testid="run-budget">
              <strong>Budget exhausted.</strong> This run stopped early, so some work may not have been dispatched.
            </p>
          )}
          {run.status === "failed" && <p className="banner banner-bad" role="alert">This run failed.</p>}
          <dl className="facts">
            <div><dt>Status</dt><dd data-testid="run-status"><span className={`chip ${run.status === "done" ? "chip-ok" : run.status === "running" ? "" : run.status === "budget_exhausted" ? "chip-warn" : "chip-bad"}`}>{titleCase(run.status)}</span></dd></div>
            <div><dt>Trigger</dt><dd>{run.trigger} by <span className="mono">{run.created_by}</span></dd></div>
            <div><dt>Sim time</dt><dd><time dateTime={run.sim_at}>{fmtDateTime(run.sim_at)}</time></dd></div>
            <div><dt>Started / finished</dt><dd>{fmtDateTime(run.started_at)} / {fmtDateTime(run.finished_at)}</dd></div>
            <div><dt>Tasks</dt><dd>{run.counts.total}</dd></div>
          </dl>
          <h3>Planner</h3>
          <Provenance p={run.planner} testId="planner-provenance" />
          {tasksError && <p className="banner banner-bad" role="alert">Could not load the run's tasks: {tasksError}</p>}
          {!tasks && !tasksError && <p role="status">Loading tasks...</p>}
          {tasks && groups.map((g) => {
            const rows = tasks.filter((t) => t.department === g);
            if (rows.length === 0) return null;
            const label = g === "supervisor" ? "Supervisor" : AGENCY_DEPARTMENT_LABEL[g];
            return (
              <section key={g} aria-label={`${label} tasks`} data-testid={`run-group-${g}`}>
                <h3>{label}</h3>
                <ol className="agency-tasks">{rows.map((t) => <TaskRow key={t.task_id} t={t} run={run} />)}</ol>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}

function AgencyBody({ role }: { role: Role }) {
  const { api } = useApp();
  const staff = role === "coordinator" || role === "supervisor";
  const [org, setOrg] = useState<AgencyOrg | null>(null);
  const [orgProblem, setOrgProblem] = useState<{ kind: string; message: string } | null>(null);
  const [inbox, setInbox] = useState<AgencyInboxItem[]>([]);
  const [inboxLoaded, setInboxLoaded] = useState(false);
  const [inboxError, setInboxError] = useState<string | null>(null);
  const [runs, setRuns] = useState<AgencyRun[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [tasks, setTasks] = useState<AgencyTask[] | null>(null);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [tick, setTick] = useState(0);

  // Reload everything on mount, on a role switch and after a run. One tick first: the provider logs in as the chosen role in an
  // effect that runs after this one in the same commit, and the first request must carry the new login.
  useEffect(() => {
    let live = true;
    setOrg(null); setOrgProblem(null); setInboxLoaded(false); setRunsError(null);
    const run = async () => {
      await Promise.resolve();
      if (!live) return;
      const [o, i, r] = await Promise.allSettled([
        staff ? api.agencyOrg() : Promise.resolve(null),
        api.agencyInbox(),
        staff ? api.agencyRuns(RUN_LIMIT) : Promise.resolve([] as AgencyRun[]),
      ]);
      if (!live) return;
      if (o.status === "fulfilled") setOrg(o.value);
      else setOrgProblem(problem(o.reason));
      if (i.status === "fulfilled") { setInbox(i.value); setInboxError(null); } else setInboxError(problem(i.reason).message);
      setInboxLoaded(true);
      if (r.status === "fulfilled") {
        setRuns(r.value);
        setRunId((cur) => (cur && r.value.some((x) => x.run_id === cur) ? cur : r.value[0]?.run_id ?? null));
      } else setRunsError(problem(r.reason).message);
    };
    void run();
    return () => { live = false; };
  }, [api, role, staff, tick]);

  useEffect(() => {
    if (!runId) { setTasks(null); return; }
    let live = true;
    setTasks(null); setTasksError(null);
    Promise.resolve()
      .then(() => api.agencyTasks({ run_id: runId }))
      .then((x) => live && setTasks(x))
      .catch((e) => live && setTasksError(problem(e).message));
    return () => { live = false; };
  }, [api, runId, role, tick]);

  const runNow = async () => {
    setRunning(true);
    setRunMsg(null);
    try {
      const r = await api.runAgency();
      setRunMsg(r.created === false
        ? { ok: true, text: `The agency already ran at this time (${fmtDateTime(r.sim_at)}, ${r.run_id}). No new run was started.` }
        : { ok: true, text: `Run ${r.run_id} started at ${fmtDateTime(r.sim_at)}: ${r.counts.total} tasks.` });
      setRunId(r.run_id);
      setTick((n) => n + 1);
    } catch (e) {
      setRunMsg({ ok: false, text: `Could not run the agency: ${problem(e).message}` });
    } finally {
      setRunning(false);
    }
  };

  return (
    <main className="container" data-testid="agency-page">
      <h1>Agency</h1>
      <section className="banner banner-ok agency-principle" aria-label="Principle" data-testid="agency-principle">
        <p>{org?.principle || AGENCY_PRINCIPLE_FALLBACK}</p>
        <p className="agency-line">This page only shows work. To act on a case, open it and use the case page's own controls.</p>
      </section>

      {!staff && (
        <p className="muted" data-testid="agency-inbox-only">
          Your role sees the tasks on the cases you own. The organisation chart and the run history are for the coordinator and the supervisor.
        </p>
      )}

      <Inbox items={inbox} error={inboxError} loaded={inboxLoaded} />

      {staff && (
        <>
          {orgProblem && <p className="banner banner-bad" role={orgProblem.kind === "unavailable" ? "status" : "alert"} data-testid="agency-org-problem">{orgProblem.message}</p>}
          {!org && !orgProblem && <p role="status">Loading the organisation...</p>}
          {org && <OrgChart org={org} />}

          <section className="panel" aria-labelledby="runs-h" data-testid="agency-runs">
            <h2 id="runs-h">Runs</h2>
            {role === "supervisor" && (
              <p className="action">
                <button type="button" className="btn btn-primary" onClick={runNow} disabled={running} data-testid="run-agency">Run agency now</button>
              </p>
            )}
            {runMsg && <p className={`banner ${runMsg.ok ? "banner-ok" : "banner-bad"}`} role={runMsg.ok ? "status" : "alert"} data-testid="run-result">{runMsg.text}</p>}
            {runsError && <p className="banner banner-bad" role="alert">Could not load the runs: {runsError}</p>}
            <RunTimeline runs={runs} runId={runId} onSelect={setRunId} tasks={tasks} tasksError={tasksError} />
          </section>
        </>
      )}
    </main>
  );
}

export function AgencyPage() {
  const { role } = useApp();
  if (role === "patient") {
    return (
      <main className="container" data-testid="agency-page">
        <h1>Agency</h1>
        <p className="banner banner-warn" role="status" data-testid="agency-denied">Not available for this role. The agency is a staff view.</p>
      </main>
    );
  }
  return <AgencyBody role={role} />;
}
