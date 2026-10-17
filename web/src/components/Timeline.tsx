import type { TimelineItem } from "../api/contract";
import { NOTIFY_LABEL } from "../lib/pathway";
import { fmtDateTime, titleCase } from "../lib/format";
import { itemKey } from "../lib/derive";

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
  hand_off: "Hand-off requested",
  hand_off_accepted: "Hand-off accepted",
  hand_off_rejected: "Hand-off rejected",
  escalate: "Escalated",
  callback_requested: "Patient asked for a call-back",
  consent_updated: "Consent updated",
  draft_generated: "AI draft generated",
  draft_approved: "Draft approved by a human",
};

const SKIP_PAYLOAD = new Set(["text_en", "text_ar"]);

function payloadSummary(p: Record<string, unknown>): string {
  return Object.entries(p)
    .filter(([k]) => !SKIP_PAYLOAD.has(k))
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
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
              <div className="tl-head"><strong>Escalation ladder: {it.action}</strong><time>{fmtDateTime(it.at)}</time></div>
              <div>
                Notify {NOTIFY_LABEL[it.notify] ?? it.notify} after {it.after_breach_days} days of breach.
                {it.action === "escalate" && (it.accepted ? " A human accepted it." : " Waiting for a human to accept.")}
              </div>
            </li>
          );
        }
        const automated = /^(system:|scheduler|watchdog|model:)/.test(it.actor);
        return (
          <li key={itemKey(it, i)} className={`tl tl-event ${it.type === "hand_off_rejected" ? "tl-rejected" : ""}`}>
            <div className="tl-head">
              <strong>{EVENT_LABEL[it.type] ?? titleCase(it.type)}</strong>
              <time>{fmtDateTime(it.at)}</time>
            </div>
            <div className="muted">
              #{it.seq} · {it.actor}{automated ? " (automated)" : ""}
              {Object.keys(it.payload).length > 0 && <> · {payloadSummary(it.payload)}</>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
