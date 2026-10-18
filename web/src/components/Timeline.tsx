import type { ActorKind, CaseEvent, TimelineItem } from "../api/contract";
import { STAFF } from "../i18n/strings";
import { MILESTONES, RUNG_LABEL, handoffReasonLabel } from "../lib/pathway";
import { fmtDateTime, titleCase } from "../lib/format";
import { actorKindOf, actorName, actorRole, itemKey, kindOfEvent, type DraftView } from "../lib/derive";
import { WATCHDOG_TEXT, watchdogDetail } from "./WatchdogMarker";

const EVENT_LABEL: Record<string, string> = {
  case_created: "Positive result received",
  acknowledge: "Result acknowledged",
  accept_ownership: "Ownership accepted",
  contact_patient: "Patient contacted",
  patient_acknowledge: "Patient acknowledged",
  book_confirmatory: "Confirmatory test booked",
  complete_confirmatory: "Confirmatory result recorded",
  clinician_review: "Clinician reviewed",
  close: "Case closed (signed)",
  handoff_requested: "Hand-off requested",
  handoff_accepted: "Hand-off accepted",
  handoff_rejected: "Hand-off rejected",
  reassign: "Referral re-routed to a new owner",
  escalate: "Escalated",
  escalation_accepted: "Escalation accepted by a human",
  callback_requested: "Patient asked for a call-back",
  consent_updated: "Consent updated",
  contact_updated: STAFF.contactUpdated,
  reply_received: STAFF.replyReceived,
  reply_classified: STAFF.replyClassified,
  review_requested: STAFF.reviewReasons.consent_withdrawn,
  assign_ownership: STAFF.assignOwnership,
  closure_proposed: STAFF.closureProposed,
  message_drafted: "AI draft generated",
  message_approved: "Draft approved by a human",
  message_rejected: "Draft rejected by a human",
  message_sent: "Message sent to the patient",
  mark_overdue: "Marked overdue by the scheduler",
  watchdog_flagged: WATCHDOG_TEXT,
  mark_lost_contact: "Marked as lost contact",
  reopen: "Case reopened by a clinician",
};

/** The heading of an event row: the type's label, refined by the payload where the same type means two things (A21). */
function eventLabel(ev: Pick<CaseEvent, "type" | "payload">): string {
  const p = ev.payload;
  if (ev.type === "review_requested") {
    const reasons: Record<string, string> = STAFF.reviewReasons;
    return reasons[String(p.reason)] ?? STAFF.reviewReasons.consent_withdrawn;
  }
  if (ev.type === "message_sent" && p.auto === true && !p.draft_id) return STAFF.templateMessageSent;
  if (ev.type === "handoff_requested" && p.auto === true) return STAFF.autoHandoff;
  return EVENT_LABEL[ev.type] ?? titleCase(ev.type);
}

/** Draft text is never shown from the event payload any more: it is loaded through GET /cases/{id}/drafts. */
const SKIP_PAYLOAD = new Set(["text_en", "text_ar", "text", "auto", "text_ref"]); // `auto: true` is shown as the Automated chip

const CONSENT_LABEL: Record<string, string> = {
  channel_sms: "text messages", channel_whatsapp: "WhatsApp", channel_phone: "phone calls", channel_letter: "letters",
  share_with_specialist: "sharing with the specialist", withdrawn: "consent withdrawn",
};

function payloadSummary(type: string, p: Record<string, unknown>): string {
  if (type === "consent_updated") {
    return Object.entries(p)
      .map(([k, v]) => (k === "withdrawn" ? (v ? "consent withdrawn" : "consent restored") : `${CONSENT_LABEL[k] ?? k.replace(/_/g, " ")}: ${v ? "allowed" : "not allowed"}`))
      .join(" · ");
  }
  if (type === "reply_received") {
    // D13: only the channel is shown. The reply text is never in an event (`text_ref` is just the id that finds it elsewhere).
    return `channel: ${String(p.channel ?? "unknown")} · ${STAFF.replyReceivedNote}`;
  }
  if (type === "reply_classified") {
    const conf = typeof p.confidence === "number" ? `${Math.round(p.confidence * 100)}%` : "unknown";
    return [
      `intent: ${String(p.intent ?? "unknown").replace(/_/g, " ")}`,
      `confidence: ${conf}`,
      ...(typeof p.model_id === "string" ? [`model: ${p.model_id}`] : []),
      p.needs_human === true ? STAFF.replyClassifiedNeedsHuman : "no person needed",
    ].join(" · ");
  }
  return Object.entries(p)
    .filter(([k]) => !SKIP_PAYLOAD.has(k))
    .map(([k, v]) => {
      const shown = type === "handoff_rejected" && k === "reason" ? handoffReasonLabel(String(v))
        : type === "assign_ownership" && k === "owner" ? actorName(String(v)) || String(v)
        : typeof v === "object" ? JSON.stringify(v) : String(v);
      return `${k.replace(/_/g, " ")}: ${shown}`;
    })
    .join(" · ");
}

/**
 * Who wrote an event. A21: the automation actor (`system:automation`) carries its own "Automated" chip, apart from the model
 * chip (an AI draft that a person must approve) and the service chip (scheduler, watchdog, notifier). A human gets none.
 * `kind` is the API's `actor_kind`; without it the kind is derived from the actor string.
 */
export function ActorTag({ actor, kind }: { actor: string; kind?: ActorKind }) {
  // The real log holds `role:opaque_id`; show the display name the API resolved (`role:name`) and keep the raw actor in the tooltip.
  const name = actorName(actor);
  const shown = name ? `${actorRole(actor)}:${name}` : actor;
  const k = kind ?? actorKindOf(actor);
  return (
    <>
      <span className="mono" title={actor}>{shown}</span>
      {k === "automation" && <> <AutomationChip /></>}
      {k === "model" && <> <span className="chip chip-auto chip-model" data-testid="automated-chip" role="img" aria-label={STAFF.modelChipLabel}>{STAFF.modelChip}</span></>}
      {k === "service" && <> <span className="chip chip-auto" data-testid="automated-chip" role="img" aria-label={STAFF.serviceChipLabel}>{STAFF.serviceChip}</span></>}
    </>
  );
}

/** The "Automated" chip (A21): done by `system:automation`, no person involved. */
export function AutomationChip() {
  return <span className="chip chip-automation" data-testid="automation-chip" role="img" aria-label={STAFF.automatedChipLabel}>{STAFF.automatedChip}</span>;
}

function AutoChip() {
  return <span className="chip chip-auto" data-testid="automated-chip">automated</span>;
}

/** The draft text for a `message_drafted` item, loaded from GET /cases/{id}/drafts, in its own language and direction. */
function DraftText({ draft }: { draft: DraftView }) {
  const text = draft.text || draft.text_en || draft.text_ar;
  if (!text) return <div className="muted">Draft text unavailable.</div>;
  return (
    <blockquote className="tl-draft" lang={draft.language} dir={draft.language === "ar" ? "rtl" : "ltr"} data-testid={`tl-draft-text-${draft.draft_id}`}>
      {text}
    </blockquote>
  );
}

export function Timeline({ items, drafts = [] }: { items: TimelineItem[]; drafts?: DraftView[] }) {
  const draftById = new Map(drafts.map((d) => [d.draft_id, d]));
  const derivedAt = (kind: string) => new Set(items.filter((i) => i.kind === kind).map((i) => i.at));
  const watchdogAt = derivedAt("watchdog");
  const bounceAt = derivedAt("bounce");
  return (
    <ol className="timeline" aria-label="Case timeline" data-testid="timeline">
      {items.map((it, i) => {
        if (it.kind === "sla_started") {
          return (
            <li key={itemKey(it, i)} className="tl tl-started" data-kind="sla_started">
              <div className="tl-head"><strong>SLA clock started</strong><time>{fmtDateTime(it.at)}</time></div>
              <div className="muted">{titleCase(it.sla)}{it.due_at ? ` is due ${fmtDateTime(it.due_at)}` : ""}.</div>
            </li>
          );
        }
        if (it.kind === "milestone_started" || it.kind === "milestone_breached" || it.kind === "milestone_met") {
          const def = MILESTONES.find((m) => m.name === it.milestone);
          const label = def?.label ?? titleCase(String(it.milestone));
          return (
            <li key={itemKey(it, i)} className={`tl ${it.kind === "milestone_breached" ? "tl-breach" : "tl-started"}`} data-kind={it.kind}>
              <div className="tl-head">
                <strong>{it.kind === "milestone_started" ? "Milestone clock started" : it.kind === "milestone_breached" ? "Milestone missed" : "Milestone met"}</strong>
                <time>{fmtDateTime(it.at)}</time>
              </div>
              <div className="muted">
                {label}
                {it.kind === "milestone_started" && it.due_at ? ` is due ${fmtDateTime(it.due_at)}` : ""}
                {it.kind === "milestone_breached" ? ` was due ${fmtDateTime(it.due_at ?? it.at)}` : ""}
                {it.kind === "milestone_met" ? (it.late ? " (met late)" : " (in time)") : ""}.
              </div>
            </li>
          );
        }
        if (it.kind === "sla_breached" || it.kind === "sla_breach") {
          return (
            <li key={itemKey(it, i)} className="tl tl-breach" data-kind="sla_breached">
              <div className="tl-head"><strong>SLA breached</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>
                {titleCase(it.sla)} was due {fmtDateTime(it.due_at ?? it.at)}
                {it.breach_days !== undefined ? `; ${it.breach_days} day${it.breach_days === 1 ? "" : "s"} late` : ""}.
              </div>
            </li>
          );
        }
        if (it.kind === "escalation") {
          return (
            <li key={itemKey(it, i)} className="tl tl-escalation" data-kind="escalation">
              <div className="tl-head"><strong>Escalation ladder: {it.rung}</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>
                Notify {RUNG_LABEL[it.rung] ?? it.target_role ?? it.rung}
                {it.after_breach_days !== undefined ? ` after ${it.after_breach_days} days of breach` : ""}.
                {it.rung !== "reminder" && (it.accepted ? " A human accepted it." : " Waiting for a human to accept.")}
              </div>
            </li>
          );
        }
        if (it.kind === "bounce") {
          return (
            <li key={itemKey(it, i)} className="tl tl-bounce" data-kind="bounce" data-testid="tl-bounce">
              <div className="tl-head"><strong>Hand-off bounced: no response</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>
                {it.to_provider ? `${it.to_provider} did not answer` : "The receiving provider did not answer"} in 3 working days.
                The case is back with the referrer and must be re-routed. <AutoChip />
              </div>
            </li>
          );
        }
        if (it.kind === "watchdog") {
          return (
            <li key={itemKey(it, i)} className="tl tl-watchdog" data-kind="watchdog" data-testid="tl-watchdog">
              <div className="tl-head"><strong><span aria-hidden="true">&#9888; </span>{WATCHDOG_TEXT}</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>{watchdogDetail({ reason: it.reason === "scheduler_down" ? "scheduler_down" : "idle", idle_days: it.idle_days })}</div>
              <div className="muted">watchdog <AutoChip /></div>
            </li>
          );
        }
        if (it.kind === "backdated") {
          return (
            <li key={itemKey(it, i)} className="tl tl-backdated muted" data-kind="backdated" data-testid="tl-backdated">
              <div className="tl-head"><strong>Backdated entry</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>
                {it.event_type ? `${EVENT_LABEL[it.event_type] ?? titleCase(it.event_type)} was` : "An event was"} recorded with a time earlier than the entry before it
                {it.previous_at ? ` (${fmtDateTime(it.previous_at)})` : ""}. It is kept in the order it was recorded.
              </div>
            </li>
          );
        }
        if (it.kind !== "event") return null;
        if (it.type === "watchdog_flagged" && watchdogAt.has(it.at)) return null; // shown once, as the derived item
        if (it.type === "handoff_rejected" && it.payload.bounced === true && bounceAt.has(it.at)) return null;
        const bounced = it.type === "handoff_rejected" && it.payload.bounced === true;
        const cls = bounced ? "tl-bounce" : it.type === "handoff_rejected" ? "tl-rejected" : it.type === "watchdog_flagged" ? "tl-watchdog" : it.type === "mark_overdue" ? "tl-overdue" : "";
        const draft = it.type === "message_drafted" ? draftById.get(String(it.payload.draft_id)) : undefined;
        return (
          <li key={itemKey(it, i)} className={`tl tl-event ${cls}`} data-event-type={it.type} data-actor-kind={kindOfEvent(it)}>
            <div className="tl-head">
              <strong>{it.type === "watchdog_flagged" && <span aria-hidden="true">&#9888; </span>}{bounced ? "Hand-off bounced: no response" : eventLabel(it)}</strong>
              <time>{fmtDateTime(it.at)}</time>
            </div>
            {it.type === "watchdog_flagged" && (
              <div>{watchdogDetail({ reason: it.payload.reason === "scheduler_down" ? "scheduler_down" : "idle", idle_days: typeof it.payload.idle_days === "number" ? it.payload.idle_days : undefined })}</div>
            )}
            {it.type === "message_drafted" && (draft ? <DraftText draft={draft} /> : <div className="muted">Draft text unavailable.</div>)}
            <div className="muted">
              #{it.seq} · <ActorTag actor={it.actor} kind={kindOfEvent(it)} />
              {Object.keys(it.payload).length > 0 && <> · {payloadSummary(it.type, it.payload)}</>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
