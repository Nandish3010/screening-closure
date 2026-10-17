import { useState } from "react";
import type { DraftApproval } from "../api/contract";
import type { DraftView } from "../lib/derive";
import { actorName } from "../lib/derive";
import { fmtDateTime } from "../lib/format";
import { ActionButton } from "./ActionButton";
import { ActorTag } from "./Timeline";

/** AI drafts with provenance. The model drafts; a human approves; nothing is "sent" before approval. */
export function DraftsPanel({
  drafts, outcomes = {}, canRequest, canDecide, busy, onRequest, onApprove, onReject, requestWhy, decideWhy, approveWhy,
}: {
  drafts: DraftView[];
  /** What the approve call answered this session (sent or blocked and why): the draft list alone cannot say. */
  outcomes?: Record<string, DraftApproval>;
  canRequest: boolean;
  canDecide: boolean;
  busy?: boolean;
  onRequest: () => void;
  onApprove: (draftId: string) => void;
  onReject: (draftId: string, reason: string) => void;
  requestWhy?: string | null;
  decideWhy?: string | null;
  /** Extra reason approving (not rejecting) is blocked, e.g. outreach is frozen. */
  approveWhy?: string | null;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  return (
    <section className="panel" aria-labelledby="drafts-h">
      <h3 id="drafts-h">Patient message drafts</h3>
      <ActionButton testId="request-draft" busy={busy} disabledReason={canRequest ? null : requestWhy ?? "Not allowed."} onClick={onRequest}>
        Request draft message
      </ActionButton>
      {drafts.length === 0 && <p className="muted">No drafts yet.</p>}
      <ul className="drafts">
        {drafts.map((d) => {
          const reason = reasons[d.draft_id] ?? "";
          return (
            <li key={d.draft_id} data-testid={`draft-${d.draft_id}`}>
              <p className="prov">
                <span className="chip chip-ai">AI draft</span>{" "}
                Provider: <strong>{d.provider}</strong>
                {d.fallback_used && <> · <span className="chip">template fallback used</span></>}
                {" "}· generated {fmtDateTime(d.created_at)} by <ActorTag actor={d.created_by} />
              </p>
              {d.text ? (
                <div className="draft-text">
                  <p lang={d.language} dir={d.language === "ar" ? "rtl" : "ltr"}>{d.text}</p>
                </div>
              ) : (
                <p className="muted">Draft text unavailable. Request a new draft.</p>
              )}
              {d.status === "approved" && (
                <p>
                  <span className="chip chip-ok" data-testid={`approved-${d.draft_id}`}>Approved by {actorName(d.approved_by ?? "") || d.approved_by} · {fmtDateTime(d.approved_at)}</span>{" "}
                  {d.sent_at || outcomes[d.draft_id]?.sent === true
                    ? <span className="chip chip-ok" data-testid={`sent-${d.draft_id}`}>Sent{d.sent_at ? ` ${fmtDateTime(d.sent_at)}` : ""}</span>
                    : outcomes[d.draft_id]?.blocked_reason
                      ? <span className="chip chip-warn" data-testid={`blocked-${d.draft_id}`}>Approved but blocked: {outcomes[d.draft_id].blocked_reason}</span>
                      : <span className="chip" data-testid={`unsent-${d.draft_id}`}>Approved, not sent yet</span>}
                </p>
              )}
              {d.status === "rejected" && (
                <p><span className="chip chip-bad" data-testid={`rejected-${d.draft_id}`}>Rejected by {actorName(d.rejected_by ?? "") || d.rejected_by}: {d.rejection_reason}</span></p>
              )}
              {d.status === "pending" && (
                <>
                  <ActionButton primary testId={`approve-${d.draft_id}`} busy={busy} disabledReason={canDecide ? approveWhy ?? null : decideWhy ?? "Not allowed."} onClick={() => onApprove(d.draft_id)}>
                    Approve draft
                  </ActionButton>
                  <label className="field"><span>Reason for rejecting</span>
                    <input value={reason} onChange={(e) => setReasons((r) => ({ ...r, [d.draft_id]: e.target.value }))} maxLength={500} />
                  </label>
                  <ActionButton testId={`reject-${d.draft_id}`} busy={busy} disabledReason={canDecide ? (reason.trim() ? null : "Enter the reason for rejecting this draft first.") : decideWhy ?? "Not allowed."} onClick={() => onReject(d.draft_id, reason.trim())}>
                    Reject draft
                  </ActionButton>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
