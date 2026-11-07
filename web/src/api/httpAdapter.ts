/** HTTP adapter: the real API at VITE_API_URL, with bearer-token auth from POST /auth/dev-login. */
import type {
  DemoGuide, DemoResetRequested, MissedFollowupResult,
  AccessLog, AgencyInboxItem, AgencyOrg, AgencyRun, AgencyTask, AgencyTaskParams, Alert, ApiClient, CaseAgencySummary, CaseDetail, CaseEvent, CaseListParams, CaseSummary, CaseTimeline, ClosureMetrics, ConfirmatoryResult, Draft, Heartbeats, TodayParams, TodayResponse,
  DraftApproval, DraftRecord, DraftRejection, DraftRequestBody, EffortMetrics, FunnelMetrics, FunnelParams, IntegrationFeed, IntegrationMockStatus, MalaffiReplayResult, SahatnaCallbackResult, Me, PostEventBody, ReplyBody, ReplyResult, Role, TimelineItem,
} from "./contract";
import { ApiError, BACKEND_ROLE, OWNER_SCOPED_ROLES, ROLES } from "./contract";
import type { OidcClient } from "./oidc";
import { ROLE_PROVIDERS } from "./identities";
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

/** A22: a 404 means the backend has no agency yet; answer `fallback` instead of failing. */
async function notFoundAs<T>(p: Promise<T>, fallback: T): Promise<T> {
  try {
    return await p;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return fallback;
    throw e;
  }
}

export function createHttpAdapter(baseUrl: string, fetchImpl: typeof fetch = (...a) => fetch(...a), opts: { oidc?: OidcClient } = {}): ApiClient {
  const oidc = opts.oidc;
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
      throw new ApiError(0, "The server cannot be reached right now. Check the connection and try again in a moment. Nothing was changed.");
    }
  }

  async function failure(res: Response): Promise<ApiError> {
    if (res.status === 503 && (res.headers.get("Retry-After") !== null || res.headers.get("X-Shed") !== null)) {
      // HZ-45: the API refused the request under overload before running it; say so plainly and say when to retry.
      const n = Number.parseInt(res.headers.get("Retry-After") ?? "", 10);
      const secs = Number.isFinite(n) && n > 0 ? n : 1;
      return new ApiError(503, `The system is busy. Please try again in ${secs} second${secs === 1 ? "" : "s"}. Nothing was changed.`, secs);
    }
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* keep statusText */
    }
    // Plain language for what a person cannot act on: a server fault, or a route refusal worded for developers. Engine refusals
    // (409, 422: "the case cannot close without evidence") are the useful part and stay verbatim.
    if (res.status >= 500) msg = "Something went wrong on the server. Nothing was changed. Please try again in a moment.";
    else if (res.status === 403 && /^(role '|no access policy)/.test(msg)) msg = "Your role cannot do this here. Switch to a role that can, or ask a colleague who can.";
    else if (res.status === 404 && /^(Not Found|not found)$/i.test(msg)) msg = "That could not be found. It may have been removed when the demo was reset.";
    return new ApiError(res.status, msg);
  }

  /**
   * The hosted demo's one-click sign-in refusals (S1): keep the API's stable `code` and the wait from `Retry-After`, so the page can tell
   * "the demo is starting up" (retry on its own) from "too many sign-ins" (wait N seconds) from a final refusal. An edge 502/504 has no JSON body.
   */
  async function signInFailure(res: Response): Promise<ApiError> {
    const n = Number.parseInt(res.headers.get("Retry-After") ?? "", 10);
    const retryAfter = Number.isFinite(n) && n > 0 ? n : undefined;
    let msg = "";
    let code: string | undefined;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      if (typeof j.code === "string") code = j.code;
    } catch {
      /* not JSON (a proxy error page) */
    }
    if (!msg) msg = res.status === 429 ? "Too many sign-ins from this network." : res.status >= 500 ? "The demo is starting up." : res.statusText || "One-click sign-in did not work.";
    return new ApiError(res.status, msg, retryAfter, code);
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
    if (oidc) return Promise.resolve(); // A29: people sign in at the provider (oidc.signIn), not by naming a role
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

  async function requestFull<T>(path: string, init?: RequestInit, retried = false): Promise<{ data: T; headers: Headers }> {
    // Requests wait for a login in flight so a role switch never races a request.
    if (pendingLogin) await pendingLogin.catch(() => undefined);
    const used = oidc ? await oidc.accessToken() : token;
    const res = await send(path, init, used);
    if (res.status === 401 && oidc) {
      // Expired or revoked at the provider: refresh once (accessToken renews it); if that fails the app is signed out.
      if (!retried && used && (await oidc.accessToken({ force: true }))) return requestFull<T>(path, init, true);
      oidc.signOut();
    } else if (res.status === 401 && identity && !retried) {
      // Token expired or rejected: log in again once and retry.
      if (token === used) {
        const who = identity;
        identity = null;
        await login(who.role, who.name, who.provider);
      } else if (pendingLogin) await pendingLogin.catch(() => undefined);
      return requestFull<T>(path, init, true);
    }
    if (!res.ok) throw await failure(res);
    return { data: (await res.json()) as T, headers: res.headers };
  }

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    return (await requestFull<T>(path, init)).data;
  }

  function qs(params: Record<string, string | string[] | undefined>): string {
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
      else if (v !== undefined && v !== "") u.set(k, v);
    }
    const s = u.toString();
    return s ? `?${s}` : "";
  }

  /**
   * GET /cases is a page (M7-7): the API answers at most `limit` rows (default 100, maximum 500) and, when more may follow, the
   * response header `X-Next-Cursor`. This follows the cursor until it is absent, so callers still get the whole worklist, but
   * never more than `maxRows` (default 10,000; the page loop is the client's bound, the page size the server's).
   */
  const listCases = async (params: CaseListParams = {}) => {
    const limit = Math.min(Math.max(params.limit ?? 500, 1), 500);
    const maxRows = params.maxRows ?? 10_000;
    const out: CaseSummary[] = [];
    let cursor: string | undefined;
    do {
      const { data, headers } = await requestFull<CaseSummary[]>(
        `/cases${qs({
          state: params.state, owner: params.owner,
          overdue: params.overdue ? "true" : undefined, flagged: params.flagged ? "true" : undefined,
          limit: String(limit), cursor,
        })}`,
      );
      out.push(...data);
      cursor = headers.get("X-Next-Cursor") ?? undefined;
    } while (cursor && out.length < maxRows);
    return out.map(completeSummary);
  };

  return {
    adapter: "http",
    health: () => request("/health"),
    login,
    oidc,
    demoSignIn: oidc
      ? async (role: Role): Promise<void> => {
          const res = await fetchImpl(`${base}/demo/sign-in`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ role }),
          });
          if (!res.ok) throw await signInFailure(res);
          oidc.adoptTokens((await res.json()) as Parameters<OidcClient["adoptTokens"]>[0]);
        }
      : undefined,
    async loginPatientFor(caseId: string, name = "Patient"): Promise<void> {
      if (oidc) return; // the patient's token already carries its patient_ref
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
      if (oidc) return `${BACKEND_ROLE[role]}:${name}`; // no way to learn another person's opaque id without their login
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
    postReply(id: string, body: ReplyBody) {
      return request<ReplyResult>(`/cases/${encodeURIComponent(id)}/replies`, { method: "POST", body: JSON.stringify(body) });
    },
    demoGuide: (session) => request<DemoGuide>(`/demo/guide?session=${encodeURIComponent(session)}`),
    requestDemoReset: () => request<DemoResetRequested>("/demo/reset", { method: "POST" }),
    missedFollowup: (caseId) => request<MissedFollowupResult>("/demo/missed-followup", { method: "POST", body: JSON.stringify({ case_id: caseId }) }),
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
        // With OIDC nobody "asked": the provider says who they are, and the provider tells two primary-care roles apart.
        const asked = identity && BACKEND_ROLE[identity.role] === backendRole ? identity.role : undefined;
        const byProvider = oidc ? ROLES.find((r) => BACKEND_ROLE[r] === backendRole && j.provider && ROLE_PROVIDERS[r] === j.provider) : undefined;
        const role = asked ?? byProvider ?? (ROLES.find((r) => BACKEND_ROLE[r] === backendRole) ?? (backendRole === "supervisor" ? "coordinator" : "coordinator"));
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
    getAccessLog(id): Promise<AccessLog> {
      return request<AccessLog>(`/cases/${encodeURIComponent(id)}/access-log`);
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
    async getHeartbeats(): Promise<Heartbeats | null> {
      try {
        const h = await request<{ scheduler?: Heartbeats["scheduler"]; watchdog?: Heartbeats["watchdog"] }>("/heartbeats");
        return { scheduler: h.scheduler ?? null, watchdog: h.watchdog ?? null };
      } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null; // not shipped, or this role may not read it
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
    async funnelMetrics(params: FunnelParams = {}): Promise<FunnelMetrics | null> {
      try {
        return await request<FunnelMetrics>(`/metrics/funnel${qs({ pathway: params.pathway, priority: params.priority })}`);
      } catch (e) {
        // Not shipped yet (404) or not readable by this role (403): the dashboard just leaves the funnel out.
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null;
        throw e;
      }
    },
    async integrationFeed(limit = 20): Promise<IntegrationFeed | null> {
      try {
        return await request<IntegrationFeed>(`/integrations/feed${qs({ limit: String(limit) })}`);
      } catch (e) {
        // An older backend (404) or a role that may not read it (403): the page shows the explanation instead of a feed.
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null;
        throw e;
      }
    },
    async integrationMockStatus(): Promise<IntegrationMockStatus | null> {
      try {
        return await request<IntegrationMockStatus>("/integrations/mock");
      } catch (e) {
        // 404: this deployment is not a development or declared demo one, so the routes do not exist; 403: not this role's.
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null;
        throw e;
      }
    },
    replayMalaffiResult: (redeliver = false) =>
      request<MalaffiReplayResult>("/integrations/mock/malaffi/replay", { method: "POST", body: JSON.stringify({ redeliver }) }),
    sahatnaBookingCallback: (caseId: string) =>
      request<SahatnaCallbackResult>("/integrations/mock/sahatna/booking-callback", { method: "POST", body: JSON.stringify({ case_id: caseId }) }),
    // A22 agency layer. A backend without it answers 404: the lists degrade to empty, the case summary to null, and
    // agencyOrg / runAgency let the ApiError through so the page can say "agency not available".
    agencyOrg: () => request<AgencyOrg>("/agency/org"),
    agencyRuns: (limit?: number) => notFoundAs<AgencyRun[]>(request(`/agency/runs${qs({ limit: limit === undefined ? undefined : String(limit) })}`), []),
    agencyTasks: (params: AgencyTaskParams = {}) =>
      notFoundAs<AgencyTask[]>(
        request(`/agency/tasks${qs({
          status: params.status, department: params.department, case_id: params.case_id, run_id: params.run_id,
          limit: params.limit === undefined ? undefined : String(params.limit),
        })}`),
        [],
      ),
    agencyInbox: () => notFoundAs<AgencyInboxItem[]>(request("/agency/inbox"), []),
    getToday: (p: TodayParams = {}) =>
      request<TodayResponse>(`/today${qs({
        limit: p.limit === undefined ? undefined : String(p.limit), cursor: p.cursor ?? undefined,
        agency_cursor: p.agencyCursor ?? undefined, group: p.groups,
      })}`),
    runAgency: () => request<AgencyRun>("/agency/run", { method: "POST", body: JSON.stringify({}) }),
    caseAgency: (id: string) => notFoundAs<CaseAgencySummary | null>(request(`/cases/${encodeURIComponent(id)}/agency`), null),
  };
}
