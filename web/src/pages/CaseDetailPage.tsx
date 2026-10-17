import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { can, isOwnerScoped, useApp } from "../app/AppContext";
import type {
  CaseDetail, CaseTimeline, ConsentChanges, ContactChannel, DraftApproval, DraftRecord, EscalationRung, TimelineItem,
} from "../api/contract";
import { ApiError, HANDOFF_REJECT_REASONS, PROVIDERS } from "../api/contract";
import { ActionButton } from "../components/ActionButton";
import { DraftsPanel } from "../components/DraftsPanel";
import { SignOffPanel } from "../components/SignOffPanel";
import { Timeline } from "../components/Timeline";
import {
  actorName as actorNameOf, allowedChannels, consentOf, draftViews, handoffs, hasCallbackPending, lostContact, overdueInfo,
  patientLang, pendingEscalation, rejectionReasons,
} from "../lib/derive";
import { fmtDateTime, titleCase } from "../lib/format";
import { ESCALATION_LADDER, HANDOFF_RESPONSE_WORKING_DAYS, RUNG_LABEL, handoffReasonLabel } from "../lib/pathway";
import { WatchdogMarker, watchdogDetail } from "../components/WatchdogMarker";
import { RiskBar, StatePill } from "./CoordinatorPage";

const CHANNEL_NAME: Record<string, string> = { sms: "text messages", whatsapp: "WhatsApp", phone: "phone calls", letter: "letters" };
/** Owners the coordinator can re-route a bounced or rejected referral to (demo list). */
const REASSIGN_TARGETS = ["primary_care_owner:Dr. Layla Haddad", "specialist_owner:Dr. Karim Nasser", "primary_care_owner:Dr. Huda Rahman"];

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const { api, role, actor, actorName, provider: myProvider, refreshSim, simTime } = useApp();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [timeline, setTimeline] = useState<CaseTimeline | null>(null);
  const [draftRecords, setDraftRecords] = useState<DraftRecord[]>([]);
  const [outcomes, setOutcomes] = useState<Record<string, DraftApproval>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  // Local form state
  const [provider, setProvider] = useState<string>(PROVIDERS[0]);
  const [rung, setRung] = useState<EscalationRung>("supervisor");
  const [reopenReason, setReopenReason] = useState("");
  const [channel, setChannel] = useState<ContactChannel>("phone");
  const [appointment, setAppointment] = useState("");
  const [appointmentDate, setAppointmentDate] = useState("");
  const [resultRef, setResultRef] = useState("");
  const [declineReason, setDeclineReason] = useState<string>(HANDOFF_REJECT_REASONS[0]);
  const [reassignTo, setReassignTo] = useState(REASSIGN_TARGETS[0]);

  const reload = useCallback(async () => {
    const [d, t, recs] = await Promise.all([
      api.getCase(caseId),
      api.getTimeline(caseId),
      // The draft texts live here, not on the timeline events. A role that may not list drafts sees the events only.
      api.listDrafts(caseId).catch(() => [] as DraftRecord[]),
    ]);
    setDetail(d);
    setTimeline(t);
    setDraftRecords(recs);
  }, [api, caseId]);

  useEffect(() => {
    reload().catch((e) => setError(e instanceof ApiError && e.status === 404 ? "Case not found." : String(e.message ?? e)));
  }, [reload]);

  /** Every action goes through the client, then refreshes. Engine refusals are shown verbatim. */
  const act = useCallback(
    async <T,>(fn: () => Promise<T>, okMessage: string | ((result: T) => string)) => {
      setBusy(true);
      setFlash(null);
      setError(null);
      try {
        const result = await fn();
        await reload();
        refreshSim();
        setFlash(typeof okMessage === "function" ? okMessage(result) : okMessage);
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
  const draftList = useMemo(() => draftViews(draftRecords, events), [draftRecords, events]);
  const handoffList = useMemo(() => handoffs(events), [events]);
  const rejections = useMemo(() => rejectionReasons(events).filter((r) => !r.bounced), [events]);
  const consent = useMemo(() => detail?.consent ?? consentOf(events), [detail?.consent, events]);
  const pendingEsc = useMemo(() => pendingEscalation(events), [events]);
  const lost = useMemo(() => lostContact(events), [events]);

  if (!detail) {
    return (
      <main className="container">
        <p><Link to="/coordinator">&larr; Worklist</Link></p>
        {error ? <p className="banner banner-bad" role="alert">{error}</p> : <p role="status">Loading case...</p>}
      </main>
    );
  }

  const { state } = detail;
  const breach = timeline?.items.find((i): i is Extract<TimelineItem, { kind: "sla_breached" | "sla_breach" }> => i.kind === "sla_breached" || i.kind === "sla_breach");
  const closed = state === "closed";
  const od = overdueInfo(detail, timeline?.sim_time ?? simTime);
  const frozen = detail.consent_frozen;
  const frozenWhy = frozen ? "Patient consent is frozen. Do not contact the patient until consent is re-confirmed." : null;
  const watchdog = detail.watchdog_flagged ?? null;
  const pending = detail.pending_handoff ?? null;
  const last = detail.last_handoff ?? null;
  const bounced = last?.outcome === "rejected" && last.bounced === true && state === "referral_rejected" ? last : null;

  // Owner scoping (A5): a clinician acts only on a case they own.
  const ownerScoped = isOwnerScoped(role);
  const mine = detail.owner_actor ? detail.owner_actor === actor : detail.owner === actorName;
  const notMine = ownerScoped && !!detail.owner && !mine;
  const scopeWhy = notMine ? `You do not own this case (the owner is ${detail.owner}). Clinicians act only on cases they own.` : null;

  const why = (action: Parameters<typeof can>[1], who: string, scoped = false) =>
    can(role, action) ? (scoped ? scopeWhy : null) : `Your role (${role}) cannot do this. Switch the role to ${who}.`;
  const closedWhy = closed ? "This case is closed." : null;
  const ownerRole = role === "owner" || role === "clinician" ? "Primary care physician" : role === "specialist" ? "Specialist" : "Care coordinator";
  const escalateRungs = ESCALATION_LADDER.filter((r) => r.rung !== "reminder");
  const rungTarget = ESCALATION_LADDER.find((r) => r.rung === rung)?.target_role ?? rung;
  const pendingApprovedDraft = draftList.find((d) => d.status === "approved");
  const answersHandoff = !!pending && ownerScoped && !!myProvider && pending.to_provider === myProvider && pending.requested_by !== actor && pending.from_owner !== actor;
  const allowed = allowedChannels(consent);

  return (
    <main className="container">
      <p><Link to="/coordinator">&larr; Worklist</Link></p>
      <h1>Case <span className="mono">{detail.case_id.slice(0, 8)}</span> <StatePill state={state} /></h1>

      <dl className="facts">
        <div><dt>Owner</dt><dd data-testid="owner">{detail.owner ?? "Unowned"}{detail.provider ? <span className="muted"> · {detail.provider}</span> : null}</dd></div>
        <div><dt>SLA clock</dt><dd data-testid="sla-clock">{closed ? "Closed" : detail.due_at ? <>Due {fmtDateTime(detail.due_at)}{od.overdue ? <> · <span className="chip chip-bad">{od.days > 0 ? `${od.days} day${od.days === 1 ? "" : "s"} overdue` : "overdue"}</span></> : " · on track"}</> : "No SLA for this step"}</dd></div>
        <div><dt>Escalation</dt><dd data-testid="escalation-level">{detail.escalation_level > 0 ? <>Level {detail.escalation_level} of 3{pendingEsc ? " · waiting for a human to accept" : ""}</> : "None"}</dd></div>
        <div><dt>Risk (orders list only)</dt><dd><RiskBar value={detail.risk_score} /></dd></div>
        <div>
          <dt>Consent</dt>
          <dd data-testid="consent-state">
            {frozen && <><span className="chip chip-warn">Frozen</span>{" "}</>}
            {consent.updated_at
              ? <>Contact allowed by: {allowed.length ? allowed.map((c) => CHANNEL_NAME[c]).join(", ") : "no channel"}; {consent.share_with_specialist ? "may share with specialist" : "sharing with specialist not allowed"} <span className="muted">(updated {fmtDateTime(consent.updated_at)})</span></>
              : "Not recorded yet (everything allowed until the patient says otherwise)"}
          </dd>
        </div>
      </dl>

      {watchdog && !closed && (
        <p className="banner banner-watchdog" role="alert" data-testid="watchdog-banner">
          <WatchdogMarker flag={watchdog} /> {watchdogDetail(watchdog)} Someone needs to take it.
        </p>
      )}
      {od.overdue && !closed && (
        <p className="banner banner-bad" role="status">
          This case is {od.days > 0 ? `${od.days} day${od.days === 1 ? "" : "s"}` : "now"} past its SLA{breach ? ` (${titleCase(breach.sla)})` : ""}. See the timeline for the escalation ladder.
        </p>
      )}
      {frozen && !closed && (
        <p className="banner banner-warn" role="status" data-testid="consent-frozen-banner">
          Patient consent is frozen{detail.review_reason ? " and the case is flagged for human review (the patient withdrew consent)" : ""}. Contacting the patient, drafting and sending messages, and new hand-offs are disabled until consent is re-confirmed.
        </p>
      )}
      {bounced && (
        <p className="banner banner-bad" role="alert" data-testid="handoff-bounced">
          <strong>Hand-off bounced: no response.</strong> {bounced.to_provider || "The receiving provider"} did not answer in {HANDOFF_RESPONSE_WORKING_DAYS} working days. The case is back with {detail.owner ?? "the referrer"} ({fmtDateTime(bounced.at)}); it must be re-routed within 2 working days or it escalates.
        </p>
      )}
      {pending && !closed && (
        <p className="banner banner-warn" role="status" data-testid="handoff-pending">
          <strong>Waiting for acceptance.</strong> Hand-off to {pending.to_provider} requested {fmtDateTime(pending.requested_at)}. {detail.owner ?? "The owner"} stays responsible until they accept.
        </p>
      )}
      {lost && !closed && <p className="banner banner-warn" role="status" data-testid="lost-contact-banner">This case is marked as lost contact. Recording a new patient contact clears it.</p>}
      {hasCallbackPending(events) && (
        <p className="banner banner-warn" role="status" data-testid="callback-banner">The patient asked for a call-back ("I don't understand this"). Please phone them.</p>
      )}
      {rejections.length > 0 && (
        <section className="banner banner-warn" aria-label="Hand-off rejections" data-testid="rejections">
          <strong>Hand-off rejected</strong>
          <ul>{rejections.map((r, i) => <li key={i}>{r.provider}: {handoffReasonLabel(r.reason)} <span className="muted">({fmtDateTime(r.at)})</span></li>)}</ul>
        </section>
      )}
      {error && <p className="banner banner-bad" role="alert">{error}</p>}
      {flash && <p className="banner banner-ok" role="status" data-testid="flash">{flash}</p>}

      <div className="columns">
        <div>
          <section className="panel" aria-labelledby="next-h">
            <h3 id="next-h">Next step</h3>
            {scopeWhy && state !== "acknowledged" && <p className="muted" data-testid="not-owner">{scopeWhy}</p>}
            {state === "result_received" && (
              <ActionButton primary testId="acknowledge" busy={busy} disabledReason={why("acknowledge", "coordinator")} onClick={() => act(() => post("acknowledge", {}), "Result acknowledged.")}>
                Acknowledge result
              </ActionButton>
            )}
            {state === "acknowledged" && (
              <ActionButton primary testId="accept-ownership" busy={busy} disabledReason={why("accept", "owner, specialist or clinician")} onClick={() => act(() => post("accept_ownership", { owner_role: ownerRole }), "You now own this case.")}>
                Accept ownership
              </ActionButton>
            )}
            {state === "owner_accepted" && (
              <>
                <label className="field"><span>Channel used</span>
                  <select value={channel} onChange={(e) => setChannel(e.target.value as ContactChannel)}>
                    <option value="phone">Phone call</option><option value="sms">Text message</option><option value="whatsapp">WhatsApp</option><option value="letter">Letter</option>
                  </select>
                </label>
                <ActionButton primary testId="contact-patient" busy={busy} disabledReason={frozenWhy ?? why("contact", "coordinator or owner", true)} onClick={() => act(() => post("contact_patient", { channel, ...(pendingApprovedDraft ? { draft_id: pendingApprovedDraft.draft_id } : {}) }), "Patient contact recorded.")}>
                  Record patient contacted
                </ActionButton>
                <p className="muted">{pendingApprovedDraft ? "An approved draft will be linked to this contact." : "Tip: request and approve a draft message first."}</p>
              </>
            )}
            {state === "patient_contacted" && <p className="muted">Waiting for the patient to acknowledge on their page.</p>}
            {state === "patient_acknowledged" && (
              <>
                <label className="field"><span>Appointment reference</span><input value={appointment} onChange={(e) => setAppointment(e.target.value)} placeholder="APPT-2026-0001" /></label>
                <label className="field"><span>Appointment date (needed for an appointment confirmation message)</span><input type="date" value={appointmentDate} onChange={(e) => setAppointmentDate(e.target.value)} /></label>
                <ActionButton primary testId="book" busy={busy} disabledReason={why("book", "coordinator or owner", true) ?? (appointment.trim() ? null : "Enter the appointment reference first.")} onClick={() => act(() => post("book_confirmatory", { appointment_ref: appointment.trim(), ...(appointmentDate ? { appointment_date: appointmentDate } : {}) }), "Booking recorded.")}>
                  Record booking
                </ActionButton>
              </>
            )}
            {state === "confirmatory_booked" && (
              <>
                <label className="field"><span>Confirmatory result reference</span><input value={resultRef} onChange={(e) => setResultRef(e.target.value)} placeholder="DiagnosticReport/colonoscopy-0001" /></label>
                <ActionButton primary testId="record-result" busy={busy} disabledReason={why("recordResult", "specialist", true) ?? (resultRef.trim() ? null : "Enter the result reference first.")} onClick={() => act(() => post("complete_confirmatory", { result_ref: resultRef.trim() }), "Confirmatory result recorded.")}>
                  Record confirmatory result
                </ActionButton>
              </>
            )}
            {(state === "confirmatory_completed" || state === "clinician_reviewed" || state === "reopened") && (
              <SignOffPanel
                role={role}
                busy={busy}
                needsReview={state !== "clinician_reviewed"}
                extraWhy={scopeWhy}
                onSignOff={(evidence, signer) =>
                  act(async () => {
                    if (state !== "clinician_reviewed") await post("clinician_review", { signed_by: signer });
                    await post("close", { evidence_ref: evidence, signed_by: signer });
                  }, "Case closed with evidence and sign-off.")
                }
              />
            )}
            {state === "referral_rejected" && (
              <div data-testid="reassign-panel">
                <p className="muted">The referral was rejected or bounced. A coordinator or supervisor can re-route it, or the owner can request a new hand-off.</p>
                <label className="field"><span>New owner</span>
                  <select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)}>
                    {REASSIGN_TARGETS.map((t) => <option key={t} value={t}>{actorNameOf(t)}</option>)}
                  </select>
                </label>
                <ActionButton primary testId="reassign" busy={busy} disabledReason={why("reassign", "coordinator")} onClick={() => act(() => post("reassign", { owner: reassignTo }), `Referral re-routed to ${actorNameOf(reassignTo)}.`)}>
                  Re-route to new owner
                </ActionButton>
              </div>
            )}
            {closed && <p>This case is closed. The audit trail below is final unless a clinician reopens it.</p>}
            {!closed && (
              <ActionButton testId="mark-lost-contact" busy={busy} disabledReason={lost ? "Already marked as lost contact." : why("markLostContact", "coordinator or owner", true)} onClick={() => act(() => post("mark_lost_contact", {}), "Marked as lost contact.")}>
                Mark patient as lost contact
              </ActionButton>
            )}
          </section>

          {closed && (
            <section className="panel" aria-labelledby="reopen-h" data-testid="reopen-panel">
              <h3 id="reopen-h">Reopen case</h3>
              <p className="muted">Only a clinician can reopen a closed case. The reason goes on the audit trail.</p>
              <label className="field"><span>Reason for reopening</span><input value={reopenReason} onChange={(e) => setReopenReason(e.target.value)} placeholder="New finding on the colonoscopy report" /></label>
              <ActionButton
                testId="reopen"
                busy={busy}
                disabledReason={why("reopen", "owner, specialist or clinician", true) ?? (reopenReason.trim() ? null : "Enter the reason for reopening first.")}
                onClick={() => act(async () => { await post("reopen", { reason: reopenReason.trim() }); setReopenReason(""); }, "Case reopened.")}
              >
                Reopen case
              </ActionButton>
            </section>
          )}

          <section className="panel" aria-labelledby="ho-h">
            <h3 id="ho-h">Hand off to provider</h3>
            {pending && !closed ? (
              <div data-testid="handoff-waiting">
                <p>
                  <span className="chip chip-warn">Waiting for acceptance</span> by {pending.to_provider} since {fmtDateTime(pending.requested_at)}
                  <span className="muted"> (requested by {actorNameOf(pending.requested_by) || pending.requested_by}). Ownership does not move until they accept; if nobody answers in {HANDOFF_RESPONSE_WORKING_DAYS} working days the scheduler bounces it.</span>
                </p>
                {answersHandoff && (
                  <>
                    <ActionButton primary testId="accept-handoff" busy={busy} disabledReason={null} onClick={() => act(() => post("handoff_accepted", {}), "Hand-off accepted: you now own this case.")}>
                      Accept hand-off to {pending.to_provider}
                    </ActionButton>
                    <label className="field"><span>Reason for declining</span>
                      <select value={declineReason} onChange={(e) => setDeclineReason(e.target.value)}>
                        {HANDOFF_REJECT_REASONS.map((r) => <option key={r} value={r}>{handoffReasonLabel(r)}</option>)}
                      </select>
                    </label>
                    <ActionButton testId="reject-handoff" busy={busy} disabledReason={null} onClick={() => act(() => post("handoff_rejected", { reason: declineReason }), "Hand-off declined.")}>
                      Decline hand-off
                    </ActionButton>
                  </>
                )}
              </div>
            ) : notMine ? (
              <>
                <p className="muted" data-testid="handoff-not-owner">{scopeWhy} To take part, ask for the case to be handed to your own provider.</p>
                <ActionButton testId="request-my-provider" busy={busy} disabledReason={closedWhy ?? frozenWhy ?? (myProvider ? null : "Your login has no provider.")} onClick={() => act(() => post("handoff_requested", { to_provider: myProvider }), `Hand-off to ${myProvider} requested. Waiting for acceptance.`)}>
                  Request handoff to my provider{myProvider ? ` (${myProvider})` : ""}
                </ActionButton>
              </>
            ) : (
              <>
                <label className="field"><span>Provider</span>
                  <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                    {PROVIDERS.map((p) => <option key={p}>{p}</option>)}
                  </select>
                </label>
                <ActionButton testId="hand-off" busy={busy} disabledReason={closedWhy ?? frozenWhy ?? why("handOff", "owner, specialist or clinician (the case owner)", true) ?? (detail.owner ? null : "Nobody owns this case yet: accept ownership first.")} onClick={() => act(() => post("handoff_requested", { to_provider: provider }), "Hand-off requested. Waiting for acceptance.")}>
                  Request hand-off
                </ActionButton>
              </>
            )}
            {last && last.outcome === "accepted" && (
              <p data-testid="handoff-accepted"><span className="chip chip-ok">Accepted</span> by {last.to_provider} ({actorNameOf(last.by) || last.by}, {fmtDateTime(last.at)})</p>
            )}
            {handoffList.length > 0 && (
              <ul className="plain">
                {handoffList.map((h, i) => (
                  <li key={i}>{h.provider}: <span className={`chip ${h.status === "rejected" ? "chip-bad" : h.status === "accepted" ? "chip-ok" : ""}`}>{h.bounced ? "bounced (no response)" : h.status}</span>{h.reason && !h.bounced ? ` - ${handoffReasonLabel(h.reason)}` : ""}</li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel" aria-labelledby="esc-h">
            <h3 id="esc-h">Escalate</h3>
            <label className="field"><span>Escalate to</span>
              <select value={rung} onChange={(e) => setRung(e.target.value as EscalationRung)}>
                {escalateRungs.map((r) => <option key={r.rung} value={r.rung}>{RUNG_LABEL[r.rung]} (after {r.after_breach_days} days)</option>)}
              </select>
            </label>
            <ActionButton testId="escalate" busy={busy} disabledReason={closedWhy ?? why("escalate", "coordinator")} onClick={() => act(() => post("escalate", { rung, target_role: rungTarget }), `Escalated to ${RUNG_LABEL[rung]}.`)}>
              Escalate
            </ActionButton>
            {pendingEsc && !closed && (
              <div data-testid="pending-escalation">
                <p className="muted">Escalated to {RUNG_LABEL[pendingEsc.rung as EscalationRung] ?? pendingEsc.rung} on {fmtDateTime(pendingEsc.at)}. Waiting for a human to accept it.</p>
                <ActionButton testId="accept-escalation" busy={busy} disabledReason={why("acceptEscalation", "coordinator or owner", true)} onClick={() => act(() => post("escalation_accepted", { rung: pendingEsc.rung }), "Escalation accepted.")}>
                  Accept escalation
                </ActionButton>
              </div>
            )}
          </section>

          <section className="panel" aria-labelledby="consent-h" data-testid="consent-panel">
            <h3 id="consent-h">Patient consent</h3>
            <p className="muted">
              {frozen ? "Withdrawn: outreach is frozen and the case is flagged for human review." : "Consent is in place. The patient controls it on their own page."}
              {detail.consent_history && detail.consent_history.length > 0 ? ` ${detail.consent_history.length} change${detail.consent_history.length === 1 ? "" : "s"} on record.` : ""}
            </p>
            {!closed && (
              <ActionButton
                testId={frozen ? "consent-restore" : "consent-withdraw"}
                busy={busy}
                disabledReason={why("recordConsent", "coordinator")}
                onClick={() => act(() => post("consent_updated", (frozen ? { withdrawn: false } : { withdrawn: true }) satisfies ConsentChanges), frozen ? "Recorded: the patient restored consent." : "Recorded: the patient withdrew consent. Outreach is frozen.")}
              >
                {frozen ? "Record: patient restored consent" : "Record: patient withdrew consent"}
              </ActionButton>
            )}
          </section>

          <DraftsPanel
            drafts={draftList}
            outcomes={outcomes}
            busy={busy}
            canRequest={!closed && !frozen && can(role, "draft")}
            defaultLanguage={patientLang(events) ?? "en"}
            canDecide={can(role, "decideDraft") && !scopeWhy}
            requestWhy={closedWhy ?? frozenWhy ?? why("draft", "coordinator or owner")}
            decideWhy={why("decideDraft", "owner, specialist or clinician (the case owner)", true)}
            approveWhy={frozenWhy ?? closedWhy}
            onRequest={(body) => act(
              () => api.requestDraft(caseId, body),
              "Draft generated. The case owner must approve it before anything is sent.",
            )}
            onApprove={(id) => act(
              async () => {
                const r = await api.approveDraft(id);
                setOutcomes((o) => ({ ...o, [id]: r }));
                return r;
              },
              (r) => (r.sent === false ? `Draft approved but blocked: ${r.blocked_reason ?? "not sent"}. Nothing was sent.` : "Draft approved and sent."),
            )}
            onReject={(id, reason) => act(() => api.rejectDraft(id, reason), "Draft rejected. Nothing was sent.")}
          />
        </div>

        <section aria-labelledby="tl-h">
          <h2 id="tl-h">Timeline</h2>
          <Timeline items={timeline?.items ?? events.map((e) => ({ kind: "event" as const, ...e }))} drafts={draftList} />
        </section>
      </div>
    </main>
  );
}

