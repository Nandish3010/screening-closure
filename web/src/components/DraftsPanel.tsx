import type { DraftView } from "../lib/derive";
import { fmtDateTime } from "../lib/format";
import { ActionButton } from "./ActionButton";
import { ActorTag } from "./Timeline";

/** AI drafts with provenance. The model drafts; a human approves; nothing is "sent" before approval. */
export function DraftsPanel({
  drafts, canRequest, canApprove, busy, onRequest, onApprove, requestWhy, approveWhy,
}: {
  drafts: DraftView[];
  canRequest: boolean;
  canApprove: boolean;
  busy?: boolean;
  onRequest: () => void;
  onApprove: (draftId: string) => void;
  requestWhy?: string | null;
  approveWhy?: string | null;
}) {
  return (
    <section className="panel" aria-labelledby="drafts-h">
      <h3 id="drafts-h">Patient message drafts</h3>
      <ActionButton testId="request-draft" busy={busy} disabledReason={canRequest ? null : requestWhy ?? "Not allowed."} onClick={onRequest}>
        Request draft message
      </ActionButton>
      {drafts.length === 0 && <p className="muted">No drafts yet.</p>}
      <ul className="drafts">
        {drafts.map((d) => (
          <li key={d.draft_id} data-testid={`draft-${d.draft_id}`}>
            <p className="prov">
              <span className="chip chip-ai">AI draft</span>{" "}
              Provider: <strong>{d.provider}</strong>
              {d.fallback_used && <> · <span className="chip">template fallback used</span></>}
              {" "}· generated {fmtDateTime(d.generated_at)} by <ActorTag actor={d.generated_by} />
            </p>
            {d.text_en || d.text_ar ? (
              <div className="draft-text">
                <p lang="en" dir="ltr">{d.text_en}</p>
                <p lang="ar" dir="rtl">{d.text_ar}</p>
              </div>
            ) : (
              <p className="muted">Draft text unavailable. Request a new draft.</p>
            )}
            {d.approved_by ? (
              <p>
                <span className="chip chip-ok" data-testid={`approved-${d.draft_id}`}>Approved by {d.approved_by} · {fmtDateTime(d.approved_at)}</span>{" "}
                {d.sent_at
                  ? <span className="chip chip-ok" data-testid={`sent-${d.draft_id}`}>Sent {fmtDateTime(d.sent_at)}</span>
                  : <span className="chip" data-testid={`unsent-${d.draft_id}`}>Approved, not sent yet</span>}
              </p>
            ) : (
              <ActionButton primary testId={`approve-${d.draft_id}`} busy={busy} disabledReason={canApprove ? null : approveWhy ?? "Not allowed."} onClick={() => onApprove(d.draft_id)}>
                Approve draft
              </ActionButton>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
