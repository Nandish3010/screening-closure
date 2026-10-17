/** HTTP adapter: the real API at VITE_API_URL. Degrades gracefully where endpoints are still PENDING. */
import type {
  ApiClient, CaseDetail, CaseEvent, CaseListParams, CaseSummary, CaseTimeline, ClosureMetrics, Draft,
  DraftApproval, Me, PostEventBody, TimelineItem,
} from "./contract";
import { ApiError } from "./contract";

export function createHttpAdapter(baseUrl: string, fetchImpl: typeof fetch = (...a) => fetch(...a)): ApiClient {
  const base = baseUrl.replace(/\/+$/, "");

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await fetchImpl(`${base}${path}`, {
        ...init,
        headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
      });
    } catch (e) {
      throw new ApiError(0, `Network error: ${(e as Error).message}`);
    }
    if (!res.ok) {
      let msg = res.statusText;
      try {
        const j = await res.json();
        msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
      } catch {
        /* keep statusText */
      }
      throw new ApiError(res.status, msg);
    }
    return (await res.json()) as T;
  }

  function qs(params: Record<string, string | undefined>): string {
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") u.set(k, v);
    const s = u.toString();
    return s ? `?${s}` : "";
  }

  const listCases = (params: CaseListParams = {}) =>
    request<CaseSummary[]>(
      `/cases${qs({ state: params.state, owner: params.owner, overdue: params.overdue ? "true" : undefined })}`,
    );

  return {
    adapter: "http",
    health: () => request("/health"),
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
      const d = await request<CaseDetail>(`/cases/${encodeURIComponent(id)}`);
      if (d.sla_due_at === undefined) {
        // The live GET /cases/{id} has no SLA fields yet: borrow them from the list row.
        try {
          const row = (await listCases()).find((c) => c.case_id === id);
          if (row) return { ...d, owner: row.owner, sla_due_at: row.sla_due_at, overdue_days: row.overdue_days, risk_score: row.risk_score };
        } catch {
          /* optional */
        }
      }
      return d;
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
    postEvent(id, body: PostEventBody) {
      const withKey = { event_id: globalThis.crypto?.randomUUID?.(), ...body, payload: body.payload ?? {} };
      return request<CaseDetail>(`/cases/${encodeURIComponent(id)}/events`, { method: "POST", body: JSON.stringify(withKey) });
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
