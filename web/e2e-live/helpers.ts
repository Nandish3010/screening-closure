/**
 * Helpers for the live e2e: talk to the real API from Node (fetch), outside the browser, for the things a
 * person would not do in the UI (create the case, move the simulation clock as a supervisor, run a sweep).
 */
import { randomUUID } from "node:crypto";

export const API = process.env.LIVE_API_URL ?? "http://localhost:8000";
export const FHIR = process.env.LIVE_FHIR_URL ?? "http://localhost:8080/fhir";
// The development scheduler token (docker compose runs with the defaults unless .env overrides them).
const SCHEDULER_TOKEN = process.env.SCHEDULER_SERVICE_TOKEN ?? "dev-only-scheduler-service-token";

export interface Login { token: string; user_id: string; role: string; display_name: string }

async function json(res: Response): Promise<any> {
  const text = await res.text();
  try { return JSON.parse(text); } catch { return text; }
}

export async function login(role: string, name: string, extra: Record<string, string> = {}): Promise<Login> {
  const res = await fetch(`${API}/auth/dev-login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role, name, ...extra }),
  });
  if (!res.ok) throw new Error(`dev-login ${role}: HTTP ${res.status} ${await res.text()}`);
  return (await res.json()) as Login;
}

export async function call(token: string, method: string, path: string, body?: unknown): Promise<{ status: number; body: any }> {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return { status: res.status, body: await json(res) };
}

/** Why the live suite cannot run, or null when the stack is up and seeded. */
export async function stackProblem(): Promise<string | null> {
  try {
    const h = await fetch(`${API}/health`, { signal: AbortSignal.timeout(4000) });
    if (!h.ok) return `the API at ${API} answered HTTP ${h.status}`;
  } catch {
    return `the API at ${API} is not reachable (start the stack with \`make up\`)`;
  }
  try {
    const coordinator = await login("coordinator", "Maryam Al Suwaidi");
    const cases = await call(coordinator.token, "GET", "/cases");
    if (cases.status !== 200 || !Array.isArray(cases.body) || cases.body.length === 0) {
      return "the stack has no cases (seed it with `make demo-seed`)";
    }
    const fhir = await fetch(`${FHIR}/metadata`, { signal: AbortSignal.timeout(8000) });
    if (!fhir.ok) return `HAPI at ${FHIR} answered HTTP ${fhir.status}`;
  } catch (e) {
    return `the stack is not ready: ${(e as Error).message}`;
  }
  return null;
}

/** A new case for a seeded patient (so the Patient resolves in HAPI), created now on the sim clock. */
export async function createCase(): Promise<{ caseId: string; patientRef: string }> {
  const coordinator = await login("coordinator", "Maryam Al Suwaidi");
  const first = (await call(coordinator.token, "GET", "/cases")).body[0];
  const caseId = randomUUID();
  const created = await call(coordinator.token, "POST", "/cases", { case_id: caseId, patient_ref: first.patient_ref });
  if (created.status !== 201) throw new Error(`create case: HTTP ${created.status} ${JSON.stringify(created.body)}`);
  return { caseId, patientRef: first.patient_ref };
}

export async function simTime(): Promise<string> {
  return (await (await fetch(`${API}/health`)).json()).sim_time;
}

/** Supervisor only, as in the real API: move the simulation clock forward. */
export async function advanceClock(days: number): Promise<string> {
  const supervisor = await login("supervisor", "Dr. Noura Al Hammadi");
  const r = await call(supervisor.token, "POST", "/clock/advance", { days });
  if (r.status !== 200) throw new Error(`clock/advance: HTTP ${r.status} ${JSON.stringify(r.body)}`);
  return r.body.sim_time;
}

/** Run one scheduler sweep now (the running scheduler container does the same every 10 s of wall time). */
export async function sweep(): Promise<any> {
  const res = await fetch(`${API}/scheduler/sweep`, { method: "POST", headers: { Authorization: `Bearer ${SCHEDULER_TOKEN}` } });
  if (!res.ok) throw new Error(`scheduler/sweep: HTTP ${res.status} ${await res.text()}`);
  return res.json();
}

export async function getCase(caseId: string): Promise<any> {
  const coordinator = await login("coordinator", "Maryam Al Suwaidi");
  return (await call(coordinator.token, "GET", `/cases/${caseId}`)).body;
}

export async function fhirExists(ref: string): Promise<boolean> {
  return (await fetch(`${FHIR}/${ref}`)).status === 200;
}
