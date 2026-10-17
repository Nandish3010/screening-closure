import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { can, useApp } from "../app/AppContext";
import type { CaseDetail, CaseTimeline, ContactChannel, TimelineItem } from "../api/contract";
import { ApiError, PROVIDERS } from "../api/contract";
import { ActionButton } from "../components/ActionButton";
import { DraftsPanel } from "../components/DraftsPanel";
import { SignOffPanel } from "../components/SignOffPanel";
import { Timeline } from "../components/Timeline";
import { consentState, drafts as draftsOf, handoffs, hasCallbackPending, rejectionReasons } from "../lib/derive";
import { fmtDateTime, titleCase } from "../lib/format";
import { ESCALATION_LADDER, NOTIFY_LABEL } from "../lib/pathway";
import { RiskBar, StatePill } from "./CoordinatorPage";

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const { api, role, actor, actorName, refreshSim } = useApp();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [timeline, setTimeline] = useState<CaseTimeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  // Local form state
  const [provider, setProvider] = useState<string>(PROVIDERS[0]);
  const [note, setNote] = useState("");
  const [notify, setNotify] = useState("owner_supervisor");
  const [channel, setChannel] = useState<ContactChannel>("phone");
  const [appointment, setAppointment] = useState("");
  const [resultRef, setResultRef] = useState("");

  const reload = useCallback(async () => {
    const [d, t] = await Promise.all([api.getCase(caseId), api.getTimeline(caseId)]);
    setDetail(d);
    setTimeline(t);
  }, [api, caseId]);

  useEffect(() => {
    reload().catch((e) => setError(e instanceof ApiError && e.status === 404 ? "Case not found." : String(e.message ?? e)));
  }, [reload]);

  /** Every action posts an event through the client, then refreshes. Engine refusals are shown verbatim. */
  const act = useCallback(
    async (fn: () => Promise<unknown>, okMessage: string) => {
      setBusy(true);
      setFlash(null);
      setError(null);
      try {
        await fn();
        await reload();
        refreshSim();
        setFlash(okMessage);
      } catch (e) {
        setError(e instanceof ApiError ? `The engine refused this: ${e.message}` : `Something went wrong: ${(e as Error).message}`);
      } finally {
        setBusy(false);
      }
    },
    [reload, refreshSim],
  );

  const post = useCallback(
    (type: string, payload: Record<string, unknown>) => api.postEvent(caseId, { type, actor, payload }),
    [api, actor, caseId],
  );

  const events = detail?.events ?? [];
  const draftList = useMemo(() => draftsOf(events), [events]);
  const handoffList = useMemo(() => handoffs(events), [events]);
  const rejections = useMemo(() => rejectionReasons(events), [events]);
  const consent = useMemo(() => consentState(events), [events]);

  if (!detail) {
    return (
      <main className="container">
        <p><Link to="/coordinator">&larr; Worklist</Link></p>
        {error ? <p className="banner banner-bad" role="alert">{error}</p> : <p role="status">Loading case...</p>}
      </main>
    );
  }

  const { state } = detail;
  const breach = timeline?.items.find((i): i is Extract<TimelineItem, { kind: "sla_breach" }> => i.kind === "sla_breach");
  const closed = state === "closed";
  const od = detail.overdue_days ?? 0;
  const why = (action: Parameters<typeof can>[1], who: string) =>
    can(role, action) ? null : `Your role (${role}) cannot do this. Switch the role to ${who}.`;
  const closedWhy = closed ? "This case is closed." : null;
  const ownerRole = role === "owner" ? "Primary care physician" : "Care coordinator";
  const escalateRungs = ESCALATION_LADDER.filter((r) => r.action === "escalate");
  const pendingApprovedDraft = draftList.find((d) => d.approved_by);

  return (
    <main className="container">
      <p><Link to="/coordinator">&larr; Worklist</Link></p>
      <h1>Case <span className="mono">{detail.case_id.slice(0, 8)}</span> <StatePill state={state} /></h1>

      <dl className="facts">
        <div><dt>Owner</dt><dd>{detail.owner ?? "Unowned"}</dd></div>
        <div><dt>SLA clock</dt><dd data-testid="sla-clock">{closed ? "Closed" : detail.sla_due_at ? <>Due {fmtDateTime(detail.sla_due_at)}{od > 0 ? <> · <span className="chip chip-bad">{od} day{od === 1 ? "" : "s"} overdue</span></> : " · on track"}</> : "No SLA for this step"}</dd></div>
        <div><dt>Risk (orders list only)</dt><dd><RiskBar value={detail.risk_score} /></dd></div>
        <div><dt>Consent</dt><dd data-testid="consent-state">{consent.updated_at ? <>Contact by {consent.channel}; {consent.share_with_specialist ? "may share with specialist" : "sharing with specialist not allowed"} <span className="muted">(updated {fmtDateTime(consent.updated_at)})</span></> : "Not recorded yet"}</dd></div>
      </dl>

      {od > 0 && !closed && (
        <p className="banner banner-bad" role="status">
          This case is {od} day{od === 1 ? "" : "s"} past its SLA{breach ? ` (${titleCase(breach.sla)})` : ""}. See the timeline for the escalation ladder.
        </p>
      )}
      {hasCallbackPending(events) && (
        <p className="banner banner-warn" role="status" data-testid="callback-banner">The patient asked for a call-back ("I don't understand this"). Please phone them.</p>
      )}
      {rejections.length > 0 && (
        <section className="banner banner-warn" aria-label="Hand-off rejections" data-testid="rejections">
          <strong>Hand-off rejected</strong>
          <ul>{rejections.map((r, i) => <li key={i}>{r.provider}: {r.reason} <span className="muted">({fmtDateTime(r.at)})</span></li>)}</ul>
        </section>
      )}
      {error && <p className="banner banner-bad" role="alert">{error}</p>}
      {flash && <p className="banner banner-ok" role="status" data-testid="flash">{flash}</p>}

      <div className="columns">
        <div>
          <section className="panel" aria-labelledby="next-h">
            <h3 id="next-h">Next step</h3>
            {state === "result_received" && (
              <ActionButton primary testId="acknowledge" busy={busy} disabledReason={why("acknowledge", "coordinator")} onClick={() => act(() => post("acknowledge", {}), "Result acknowledged.")}>
                Acknowledge result
              </ActionButton>
            )}
            {state === "acknowledged" && (
              <ActionButton primary testId="accept-ownership" busy={busy} disabledReason={why("accept", "coordinator or owner")} onClick={() => act(() => post("accept_ownership", { owner: actorName, owner_role: ownerRole }), "You now own this case.")}>
                Accept ownership
              </ActionButton>
            )}
            {state === "owner_accepted" && (
              <>
                <label className="field"><span>Channel used</span>
                  <select value={channel} onChange={(e) => setChannel(e.target.value as ContactChannel)}>
                    <option value="phone">Phone call</option><option value="sms">Text message</option><option value="whatsapp">WhatsApp</option>
                  </select>
                </label>
                <ActionButton primary testId="contact-patient" busy={busy} disabledReason={why("contact", "coordinator or owner")} onClick={() => act(() => post("contact_patient", { channel, ...(pendingApprovedDraft ? { draft_id: pendingApprovedDraft.draft_id } : {}) }), "Patient contact recorded.")}>
                  Record patient contacted
                </ActionButton>
                <p className="muted">{pendingApprovedDraft ? "An approved draft will be linked to this contact." : "Tip: request and approve a draft message first."}</p>
              </>
            )}
            {state === "patient_contacted" && <p className="muted">Waiting for the patient to acknowledge on their page.</p>}
            {state === "patient_acknowledged" && (
              <>
                <label className="field"><span>Appointment reference</span><input value={appointment} onChange={(e) => setAppointment(e.target.value)} placeholder="APPT-2026-0001" /></label>
                <ActionButton primary testId="book" busy={busy} disabledReason={why("book", "coordinator or owner") ?? (appointment.trim() ? null : "Enter the appointment reference first.")} onClick={() => act(() => post("book_confirmatory", { appointment_ref: appointment.trim() }), "Booking recorded.")}>
                  Record booking
                </ActionButton>
              </>
            )}
            {state === "confirmatory_booked" && (
              <>
                <label className="field"><span>Confirmatory result reference</span><input value={resultRef} onChange={(e) => setResultRef(e.target.value)} placeholder="DiagnosticReport/colonoscopy-0001" /></label>
                <ActionButton primary testId="record-result" busy={busy} disabledReason={why("recordResult", "coordinator, owner or specialist") ?? (resultRef.trim() ? null : "Enter the result reference first.")} onClick={() => act(() => post("complete_confirmatory", { result_ref: resultRef.trim() }), "Confirmatory result recorded.")}>
                  Record confirmatory result
                </ActionButton>
              </>
            )}
            {(state === "confirmatory_completed" || state === "clinician_reviewed") && (
              <SignOffPanel
                role={role}
                busy={busy}
                needsReview={state === "confirmatory_completed"}
                onSignOff={(evidence, signer) =>
                  act(async () => {
                    if (state === "confirmatory_completed") await post("clinician_review", { signed_by: signer });
                    await post("close", { evidence_ref: evidence, signed_by: signer });
                  }, "Case closed with evidence and sign-off.")
                }
              />
            )}
            {closed && <p>This case is closed. The audit trail below is final.</p>}
          </section>

          <section className="panel" aria-labelledby="ho-h">
            <h3 id="ho-h">Hand off to provider</h3>
            <label className="field"><span>Provider</span>
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                {PROVIDERS.map((p) => <option key={p}>{p}</option>)}
              </select>
            </label>
            <ActionButton testId="hand-off" busy={busy} disabledReason={closedWhy ?? why("handOff", "coordinator or owner")} onClick={() => act(() => post("hand_off", { to_provider: provider }), "Hand-off requested.")}>
              Request hand-off
            </ActionButton>
            {handoffList.length > 0 && (
              <ul className="plain">
                {handoffList.map((h, i) => (
                  <li key={i}>{h.provider}: <span className={`chip ${h.status === "rejected" ? "chip-bad" : h.status === "accepted" ? "chip-ok" : ""}`}>{h.status}</span>{h.reason ? ` - ${h.reason}` : ""}</li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel" aria-labelledby="esc-h">
            <h3 id="esc-h">Escalate</h3>
            <label className="field"><span>Escalate to</span>
              <select value={notify} onChange={(e) => setNotify(e.target.value)}>
                {escalateRungs.map((r) => <option key={r.notify} value={r.notify}>{NOTIFY_LABEL[r.notify]} (after {r.after_breach_days} days)</option>)}
              </select>
            </label>
            <label className="field"><span>Note (optional)</span><input value={note} onChange={(e) => setNote(e.target.value)} /></label>
            <ActionButton testId="escalate" busy={busy} disabledReason={closedWhy ?? why("escalate", "coordinator or owner")} onClick={() => act(() => post("escalate", { notify, ...(note.trim() ? { note: note.trim() } : {}) }), `Escalated to ${NOTIFY_LABEL[notify]}.`)}>
              Escalate
            </ActionButton>
          </section>

          <DraftsPanel
            drafts={draftList}
            busy={busy}
            canRequest={!closed && can(role, "draft")}
            canApprove={can(role, "approve")}
            requestWhy={closedWhy ?? why("draft", "coordinator or owner")}
            approveWhy={why("approve", "coordinator or owner")}
            onRequest={() => act(() => api.requestDraft(caseId, actor), "Draft generated. A human must approve it before it is used.")}
            onApprove={(id) => act(() => api.approveDraft(id, actorName), "Draft approved.")}
          />
        </div>

        <section aria-labelledby="tl-h">
          <h2 id="tl-h">Timeline</h2>
          <Timeline items={timeline?.items ?? events.map((e) => ({ kind: "event" as const, ...e }))} />
        </section>
      </div>
    </main>
  );
}
