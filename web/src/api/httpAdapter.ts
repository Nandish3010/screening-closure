/** HTTP adapter: the real API at VITE_API_URL, with bearer-token auth from POST /auth/dev-login. */
import type {
  Alert, ApiClient, CaseDetail, CaseEvent, CaseListParams, CaseSummary, CaseTimeline, ClosureMetrics, ConfirmatoryResult, Draft,
  DraftApproval, DraftRecord, DraftRejection, DraftRequestBody, EffortMetrics, Me, PostEventBody, Role, TimelineItem,
} from "./contract";
import { ApiError, BACKEND_ROLE, OWNER_SCOPED_ROLES, ROLES } from "./contract";
import { completeDetail, completeSummary, registerActors } from "../lib/derive";

const PATIENT_REF_KEY = "sc.patient_ref";
/** The staff login the adapter uses on its own to read a case's `patient_ref` for a patient login (demo only). */
const LOOKUP_LOGIN = { role: "coordinator", name: "Maryam Al Suwaidi" } as const;

function storedPatientRef(): string | undefined {
  try {
    return localStorage.getItem(PATIENT_REF_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

export function createHttpAdapter(baseUrl: string, fetchImpl: typeof fetch = (...a) => fetch(...a)): ApiClient {
  const base = baseUrl.replace(/\/+$/, "");

  // ---- Auth state: the identity we last asked to be, its token, and the login in flight ----
  let identity: { role: Role; name: string; provider?: string; patientRef?: string } | null = null;
  let patientRef: string | undefined = storedPatientRef();
  let token: string | null = null;
  let pendingLogin: Promise<void> | null = null;

  async function send(path: string, init: RequestInit | undefined, bearer: string | null): Promise<Response> {
    try {
      return await fetchImpl(`${base}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
          ...init?.headers,
        },
      });
    } catch (e) {
      throw new ApiError(0, `Network error: ${(e as Error).message}`);
    }
  }

  async function failure(res: Response): Promise<ApiError> {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* keep statusText */
    }
    return new ApiError(res.status, msg);
  }

  /**
   * POST /auth/dev-login {role (backend role), name, provider?, patient_ref?} -> {token, token_type, user_id, role, display_name, ...}.
   * `provider` is sent for the owner roles only, `patient_ref` for patients only (the real API refuses a patient login
   * without one). A 404 means the backend has no auth: carry on without a token.
   */
  async function rawLogin(who: { role: string; name: string; provider?: string; patientRef?: string }): Promise<{ token: string | null; user_id?: string; role?: string } | null> {
    const body = {
      role: who.role,
      name: who.name,
      ...(who.provider ? { provider: who.provider } : {}),
      ...(who.patientRef ? { patient_ref: who.patientRef } : {}),
    };
    const res = await send("/auth/dev-login", { method: "POST", body: JSON.stringify(body) }, null);
    if (res.status === 404) return null;
    if (!res.ok) throw await failure(res);
    const j = (await res.json()) as { access_token?: string; token?: string; user_id?: string; role?: string };
    return { token: j.token ?? j.access_token ?? null, user_id: j.user_id, role: j.role };
  }

  async function devLogin(who: { role: Role; name: string; provider?: string; patientRef?: string }): Promise<string | null> {
    if (who.role === "patient" && !who.patientRef) return null; // no patient_ref yet: loginPatientFor(caseId) supplies it
    const out = await rawLogin({
      role: BACKEND_ROLE[who.role],
      name: who.name,
      provider: who.provider && OWNER_SCOPED_ROLES.includes(who.role) ? who.provider : undefined,
      patientRef: who.role === "patient" ? who.patientRef : undefined,
    });
    return out ? out.token : null;
  }

  function login(role: Role, name: string, provider?: string): Promise<void> {
    const ref = role === "patient" ? patientRef : undefined;
    if (identity && identity.role === role && identity.name === name && identity.provider === provider && identity.patientRef === ref && (token || pendingLogin)) {
      return pendingLogin ?? Promise.resolve();
    }
    const who = { role, name, provider, patientRef: ref };
    identity = who;
    token = null;
    const p = devLogin(who).then((t) => {
      if (identity === who) token = t; // ignore a login that a newer role switch has superseded
    });
    pendingLogin = p;
    const clear = () => {
      if (pendingLogin === p) pendingLogin = null;
    };
    p.then(clear, () => {
      clear();
      if (identity === who) identity = null; // allow a retry
    });
    return p;
  }

  /** actor strings (`role:user_id`) learnt by logging in, keyed by the login. */
  const actorCache = new Map<string, string>();

  async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
    // Requests wait for a login in flight so a role switch never races a request.
    if (pendingLogin) await pendingLogin.catch(() => undefined);
    const used = token;
    const res = await send(path, init, used);
    if (res.status === 401 && identity && !retried) {
      // Token expired or rejected: log in again once and retry.
      if (token === used) {
        const who = identity;
        identity = null;
        await login(who.role, who.name, who.provider);
      } else if (pendingLogin) await pendingLogin.catch(() => undefined);
      return request<T>(path, init, true);
    }
    if (!res.ok) throw await failure(res);
    return (await res.json()) as T;
  }

  function qs(params: Record<string, string | undefined>): string {
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") u.set(k, v);
    const s = u.toString();
    return s ? `?${s}` : "";
  }

  const listCases = async (params: CaseListParams = {}) =>
    (
      await request<CaseSummary[]>(
        `/cases${qs({
          state: params.state, owner: params.owner,
          overdue: params.overdue ? "true" : undefined, flagged: params.flagged ? "true" : undefined,
        })}`,
      )
    ).map(completeSummary);

  return {
    adapter: "http",
    health: () => request("/health"),
    login,
    async loginPatientFor(caseId: string, name = "Patient"): Promise<void> {
      // A patient token needs the patient's own `patient_ref`, which only staff can read from the case.
      const staff = await rawLogin(LOOKUP_LOGIN);
      if (!staff?.token) return; // no auth on this backend
      const res = await send(`/cases/${encodeURIComponent(caseId)}`, undefined, staff.token);
      if (!res.ok) throw await failure(res);
      const ref = ((await res.json()) as { patient_ref?: string }).patient_ref;
      if (!ref) throw new ApiError(422, "This case has no patient_ref, so no patient login is possible.");
      patientRef = ref;
      try {
        localStorage.setItem(PATIENT_REF_KEY, ref);
      } catch {
        /* ignore */
      }
      identity = null; // force a fresh login with the new reference
      await login("patient", name);
    },
    async actorFor(role: Role, name: string, provider?: string): Promise<string> {
      const key = `${role}|${name}|${provider ?? ""}`;
      const hit = actorCache.get(key);
      if (hit) return hit;
      const out = await rawLogin({
        role: BACKEND_ROLE[role], name,
        provider: provider && OWNER_SCOPED_ROLES.includes(role) ? provider : undefined,
      });
      const actor = out?.user_id ? `${BACKEND_ROLE[role]}:${out.user_id}` : `${BACKEND_ROLE[role]}:${name}`;
      actorCache.set(key, actor);
      return actor;
    },
    recordConfirmatoryResult(id: string) {
      return request<ConfirmatoryResult>(`/cases/${encodeURIComponent(id)}/confirmatory-result`, { method: "POST" });
    },
    async advanceClock(by) {
      const j = await request<{ sim_time: string }>("/clock/advance", { method: "POST", body: JSON.stringify(by) });
      return j.sim_time;
    },
    async me(): Promise<Me> {
      try {
        // GET /me -> {user_id, role (backend role), display_name, patient_ref?, provider?}
        const j = await request<{ user_id?: string; role?: string; display_name?: string; name?: string; provider?: string; patient_ref?: string }>("/me");
        const backendRole = j.role ?? "coordinator";
        // The UI has more roles than the backend (clinician and owner are both primary_care_owner): the role
        // this session asked to be wins when it maps to the same backend role.
        const asked = identity && BACKEND_ROLE[identity.role] === backendRole ? identity.role : undefined;
        const role = asked ?? (ROLES.find((r) => BACKEND_ROLE[r] === backendRole) ?? (backendRole === "supervisor" ? "coordinator" : "coordinator"));
        return { role, name: j.display_name ?? j.name ?? "Coordinator", user_id: j.user_id, provider: j.provider, patient_ref: j.patient_ref };
      } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.status === 0)) return { role: "coordinator", name: "Coordinator" };
        throw e;
      }
    },
    listCases,
    async getCase(id): Promise<CaseDetail> {
      const d = await request<CaseDetail & { actors?: Record<string, { display_name?: string }> }>(`/cases/${encodeURIComponent(id)}`);
      registerActors(d.actors);
      return completeDetail(d);
    },
    async getTimeline(id): Promise<CaseTimeline> {
      try {
        const t = await request<CaseTimeline & { actors?: Record<string, { display_name?: string }> }>(`/cases/${encodeURIComponent(id)}/timeline`);
        registerActors(t.actors);
        // The API's derived kinds (A9) are `handoff_bounce` and `watchdog_flag`; the renderers use
        // the shorter `bounce` and `watchdog` (the mock's names).
        const KIND: Record<string, string> = { handoff_bounce: "bounce", watchdog_flag: "watchdog" };
        return { ...t, items: t.items.map((it) => (it.kind in KIND ? ({ ...it, kind: KIND[it.kind] } as TimelineItem) : it)) };
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 404) throw e;
        // Endpoint not shipped yet: fall back to the plain event log.
        const d = await request<CaseDetail>(`/cases/${encodeURIComponent(id)}`);
        const items: TimelineItem[] = d.events.map((ev: CaseEvent) => ({ kind: "event" as const, ...ev }));
        return { case_id: id, sim_time: d.events.at(-1)?.at ?? new Date().toISOString(), items };
      }
    },
    async postEvent(id, body: PostEventBody) {
      // The backend derives the actor from the bearer token and answers 422 to a body that contains one.
      const { actor: _actor, ...rest } = body;
      const withKey = { event_id: globalThis.crypto?.randomUUID?.(), ...rest, payload: body.payload ?? {} };
      const d = await request<CaseDetail & { actors?: Record<string, { display_name?: string }> }>(
        `/cases/${encodeURIComponent(id)}/events`, { method: "POST", body: JSON.stringify(withKey) },
      );
      registerActors(d.actors);
      return completeDetail(d);
    },
    requestDraft(id, body: DraftRequestBody) {
      // The requester is the bearer token's identity; `requested_by` would be a 422.
      return request<Draft>(`/cases/${encodeURIComponent(id)}/drafts`, {
        method: "POST",
        body: JSON.stringify({ purpose: body.purpose, channel: body.channel, language: body.language }),
      });
    },
    listDrafts(id) {
      return request<DraftRecord[]>(`/cases/${encodeURIComponent(id)}/drafts`);
    },
    approveDraft(draftId) {
      // No body: the approver is the bearer token's identity.
      return request<DraftApproval>(`/drafts/${encodeURIComponent(draftId)}/approve`, { method: "POST" });
    },
    rejectDraft(draftId, body) {
      // {reason, note?}: the reason is the fixed taxonomy; the note goes only with other_with_note (A16).
      return request<DraftRejection>(`/drafts/${encodeURIComponent(draftId)}/reject`, {
        method: "POST",
        body: JSON.stringify(body.note ? { reason: body.reason, note: body.note } : { reason: body.reason }),
      });
    },
    async listAlerts(): Promise<Alert[]> {
      try {
        return await request<Alert[]>("/alerts");
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return []; // endpoint not shipped yet: no alerts
        throw e;
      }
    },
    closureMetrics: () => request<ClosureMetrics>("/metrics/closure"),
    async getEffortMetrics(): Promise<EffortMetrics | null> {
      try {
        return await request<EffortMetrics>("/metrics/effort");
      } catch (e) {
        // Not shipped yet (404) or not readable by this role (403): the dashboard just leaves the card out.
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null;
        throw e;
      }
    },
  };
}
