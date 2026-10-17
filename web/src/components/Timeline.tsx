import type { TimelineItem } from "../api/contract";
import { RUNG_LABEL, handoffReasonLabel } from "../lib/pathway";
import { fmtDateTime, titleCase } from "../lib/format";
import { isAutomatedActor, itemKey } from "../lib/derive";
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
  escalate: "Escalated",
  escalation_accepted: "Escalation accepted by a human",
  callback_requested: "Patient asked for a call-back",
  consent_updated: "Consent updated",
  message_drafted: "AI draft generated",
  message_approved: "Draft approved by a human",
  message_sent: "Message sent to the patient",
  mark_overdue: "Marked overdue by the scheduler",
  watchdog_flagged: WATCHDOG_TEXT,
  mark_lost_contact: "Marked as lost contact",
  reopen: "Case reopened by a clinician",
};

const SKIP_PAYLOAD = new Set(["text_en", "text_ar"]);

function payloadSummary(type: string, p: Record<string, unknown>): string {
  return Object.entries(p)
    .filter(([k]) => !SKIP_PAYLOAD.has(k))
    .map(([k, v]) => {
      const shown = type === "handoff_rejected" && k === "reason" ? handoffReasonLabel(String(v)) : typeof v === "object" ? JSON.stringify(v) : String(v);
      return `${k.replace(/_/g, " ")}: ${shown}`;
    })
    .join(" · ");
}

/** "Automated" chip for scheduler, watchdog, model and system actors. */
export function ActorTag({ actor }: { actor: string }) {
  return (
    <>
      <span className="mono">{actor}</span>
      {isAutomatedActor(actor) && <> <span className="chip chip-auto" data-testid="automated-chip">automated</span></>}
    </>
  );
}

export function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="timeline" aria-label="Case timeline" data-testid="timeline">
      {items.map((it, i) => {
        if (it.kind === "sla_breach") {
          return (
            <li key={itemKey(it, i)} className="tl tl-breach">
              <div className="tl-head"><strong>SLA breached</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>{titleCase(it.sla)} was due {fmtDateTime(it.due_at)}; {it.breach_days} day{it.breach_days === 1 ? "" : "s"} late.</div>
            </li>
          );
        }
        if (it.kind === "escalation") {
          return (
            <li key={itemKey(it, i)} className="tl tl-escalation">
              <div className="tl-head"><strong>Escalation ladder: {it.rung}</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>
                Notify {RUNG_LABEL[it.rung] ?? it.target_role} after {it.after_breach_days} days of breach.
                {it.rung !== "reminder" && (it.accepted ? " A human accepted it." : " Waiting for a human to accept.")}
              </div>
            </li>
          );
        }
        const cls = it.type === "handoff_rejected" ? "tl-rejected" : it.type === "watchdog_flagged" ? "tl-watchdog" : it.type === "mark_overdue" ? "tl-overdue" : "";
        return (
          <li key={itemKey(it, i)} className={`tl tl-event ${cls}`} data-event-type={it.type}>
            <div className="tl-head">
              <strong>{it.type === "watchdog_flagged" && <span aria-hidden="true">&#9888; </span>}{EVENT_LABEL[it.type] ?? titleCase(it.type)}</strong>
              <time>{fmtDateTime(it.at)}</time>
            </div>
            {it.type === "watchdog_flagged" && (
              <div>{watchdogDetail({ reason: it.payload.reason === "scheduler_down" ? "scheduler_down" : "idle", idle_days: typeof it.payload.idle_days === "number" ? it.payload.idle_days : undefined })}</div>
            )}
            <div className="muted">
              #{it.seq} · <ActorTag actor={it.actor} />
              {Object.keys(it.payload).length > 0 && <> · {payloadSummary(it.type, it.payload)}</>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
