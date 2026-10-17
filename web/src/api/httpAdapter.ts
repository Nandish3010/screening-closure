/** HTTP adapter: the real API at VITE_API_URL, with bearer-token auth from POST /auth/dev-login. */
import type {
  ApiClient, CaseDetail, CaseEvent, CaseListParams, CaseSummary, CaseTimeline, ClosureMetrics, Draft,
  DraftApproval, Me, PostEventBody, Role, TimelineItem,
} from "./contract";
import { ApiError } from "./contract";
import { completeDetail, completeSummary } from "../lib/derive";

export function createHttpAdapter(baseUrl: string, fetchImpl: typeof fetch = (...a) => fetch(...a)): ApiClient {
  const base = baseUrl.replace(/\/+$/, "");

  // ---- Auth state: the identity we last asked to be, its token, and the login in flight ----
  let identity: { role: Role; name: string } | null = null;
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

  /** POST /auth/dev-login. A 404 means the backend has no auth: carry on without a token. */
  async function devLogin(who: { role: Role; name: string }): Promise<string | null> {
    const res = await send("/auth/dev-login", { method: "POST", body: JSON.stringify(who) }, null);
    if (res.status === 404) return null;
    if (!res.ok) throw await failure(res);
    const j = (await res.json()) as { access_token?: string; token?: string };
    return j.access_token ?? j.token ?? null;
  }

  function login(role: Role, name: string): Promise<void> {
    if (identity && identity.role === role && identity.name === name && (token || pendingLogin)) return pendingLogin ?? Promise.resolve();
    const who = { role, name };
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
        await login(who.role, who.name);
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
        `/cases${qs({ state: params.state, owner: params.owner, overdue: params.overdue ? "true" : undefined })}`,
      )
    ).map(completeSummary);

  return {
    adapter: "http",
    health: () => request("/health"),
    login,
    async me(): Promise<Me> {
      try {
        return await request<Me>("/me");
      } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.status === 0)) return { role: "coordinator", name: "Coordinator" };
        throw e;
      }
    },
    listCases,
    async getCase(id): Promise<CaseDetail> {
      return completeDetail(await request<CaseDetail>(`/cases/${encodeURIComponent(id)}`));
    },
    async getTimeline(id): Promise<CaseTimeline> {
      try {
        return await request<CaseTimeline>(`/cases/${encodeURIComponent(id)}/timeline`);
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 404) throw e;
        // Endpoint not shipped yet: fall back to the plain event log.
        const d = await request<CaseDetail>(`/cases/${encodeURIComponent(id)}`);
        const items: TimelineItem[] = d.events.map((ev: CaseEvent) => ({ kind: "event" as const, ...ev }));
        return { case_id: id, sim_time: d.events.at(-1)?.at ?? new Date().toISOString(), items };
      }
    },
    async postEvent(id, body: PostEventBody) {
      const withKey = { event_id: globalThis.crypto?.randomUUID?.(), ...body, payload: body.payload ?? {} };
      return completeDetail(
        await request<CaseDetail>(`/cases/${encodeURIComponent(id)}/events`, { method: "POST", body: JSON.stringify(withKey) }),
      );
    },
    requestDraft(id, requestedBy) {
      return request<Draft>(`/cases/${encodeURIComponent(id)}/drafts`, {
        method: "POST",
        body: JSON.stringify({ requested_by: requestedBy }),
      });
    },
    approveDraft(draftId, approvedBy) {
      return request<DraftApproval>(`/drafts/${encodeURIComponent(draftId)}/approve`, {
        method: "POST",
        body: JSON.stringify({ approved_by: approvedBy }),
      });
    },
    closureMetrics: () => request<ClosureMetrics>("/metrics/closure"),
  };
}
