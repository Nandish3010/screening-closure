import { useId, useState } from "react";
import type { BlockedReason, DraftApproval, DraftChannel, DraftPurpose, DraftRequestBody, Lang } from "../api/contract";
import { DRAFT_CHANNELS, DRAFT_PURPOSES } from "../api/contract";
import type { DraftView } from "../lib/derive";
import { actorName } from "../lib/derive";
import { fmtDateTime } from "../lib/format";
import { ActionButton } from "./ActionButton";
import { ActorTag } from "./Timeline";

const PURPOSE_LABEL: Record<DraftPurpose, string> = {
  result_notification: "Result notification",
  reminder: "Reminder",
  appointment_confirmation: "Appointment confirmation",
};
const CHANNEL_LABEL: Record<DraftChannel, string> = { sms: "Text message (SMS)", whatsapp: "WhatsApp", letter: "Letter" };
const LANG_LABEL: Record<Lang, string> = { en: "English", ar: "Arabic (العربية)" };

/** Why a draft is not sent after approval (the approve answer's `blocked_reason`). */
export const BLOCKED_TEXT: Record<BlockedReason, string> = {
  consent: "the patient has switched that channel off",
  contact: "the contact details on file are flagged invalid",
  case_state: "the case changed before the send (closed, or consent withdrawn)",
};

/** `abstention_reason` is a code, optionally with detail after a colon (`validation_failed:forbidden_phrase:cancer`). */
export function abstentionText(reason: string): string {
  const [code, ...rest] = reason.split(":");
  const detail = rest.join(":");
  const label: Record<string, string> = {
    provider_unavailable: "the AI provider was unavailable",
    provider_error: "the AI provider returned an error",
    cache_miss: "there was no recorded AI output for this prompt",
    invalid_result: "the AI returned an unusable result",
    validation_failed: "the AI draft failed the safety checks",
  };
  return `${label[code] ?? code.replace(/_/g, " ")}${detail ? ` (${detail.replace(/[:,]/g, " ").replace(/_/g, " ").trim()})` : ""}`;
}

/**
 * AI drafts with provenance. The model drafts; the case owner approves or rejects; nothing is "sent" before
 * approval. A coordinator can request a draft but not decide on it, and the panel says why.
 */
export function DraftsPanel({
  drafts, outcomes = {}, defaultLanguage = "en", canRequest, canDecide, busy, onRequest, onApprove, onReject, requestWhy, decideWhy, approveWhy,
}: {
  drafts: DraftView[];
  /** What the approve call answered this session (sent or blocked and why): the draft list alone cannot say. */
  outcomes?: Record<string, DraftApproval>;
  defaultLanguage?: Lang;
  canRequest: boolean;
  canDecide: boolean;
  busy?: boolean;
  onRequest: (body: DraftRequestBody) => void;
  onApprove: (draftId: string) => void;
  onReject: (draftId: string, reason: string) => void;
  requestWhy?: string | null;
  /** Why the acting user cannot approve or reject (role or ownership). Shown once, inline. */
  decideWhy?: string | null;
  /** Extra reason approving (not rejecting) is blocked, e.g. outreach is frozen. */
  approveWhy?: string | null;
}) {
  const noteId = useId();
  const [purpose, setPurpose] = useState<DraftPurpose>("result_notification");
  const [channel, setChannel] = useState<DraftChannel>("sms");
  const [language, setLanguage] = useState<Lang | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const lang = language ?? defaultLanguage;
  const pendingCount = drafts.filter((d) => d.status === "pending").length;

  return (
    <section className="panel" aria-labelledby="drafts-h" data-testid="drafts-panel">
      <h3 id="drafts-h">Patient message drafts</h3>
      <p className="muted">The AI drafts; the case owner approves. Nothing is sent to the patient before an approval, and the owner can reject a draft with a reason.</p>

      <fieldset className="draft-form" data-testid="draft-form">
        <legend>Request a draft</legend>
        <div className="row">
          <label className="field"><span>Purpose</span>
            <select value={purpose} onChange={(e) => setPurpose(e.target.value as DraftPurpose)}>
              {DRAFT_PURPOSES.map((p) => <option key={p} value={p}>{PURPOSE_LABEL[p]}</option>)}
            </select>
          </label>
          <label className="field"><span>Channel</span>
            <select value={channel} onChange={(e) => setChannel(e.target.value as DraftChannel)}>
              {DRAFT_CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}
            </select>
          </label>
          <label className="field"><span>Language</span>
            <select value={lang} onChange={(e) => setLanguage(e.target.value as Lang)}>
              {(["en", "ar"] as const).map((l) => <option key={l} value={l}>{LANG_LABEL[l]}</option>)}
            </select>
          </label>
        </div>
        {purpose === "appointment_confirmation" && <p className="muted">Needs a booked appointment with a date.</p>}
        <ActionButton testId="request-draft" busy={busy} disabledReason={canRequest ? null : requestWhy ?? "Not allowed."} onClick={() => onRequest({ purpose, channel, language: lang })}>
          Request draft message
        </ActionButton>
      </fieldset>

      {!canDecide && (
        <p id={noteId} className="banner banner-warn" role="note" data-testid="decide-why">
          You can request drafts, but only the case owner can approve or reject them. {decideWhy}
        </p>
      )}
      {drafts.length === 0 && <p className="muted">No drafts yet.</p>}
      {canDecide && pendingCount > 0 && approveWhy && <p className="muted" data-testid="approve-why">{approveWhy} You can still reject a draft.</p>}

      <ul className="drafts">
        {drafts.map((d) => {
          const reason = reasons[d.draft_id] ?? "";
          const outcome = outcomes[d.draft_id];
          const blocked = outcome?.blocked_reason ?? null;
          const dir = d.language === "ar" ? "rtl" : "ltr";
          return (
            <li key={d.draft_id} data-testid={`draft-${d.draft_id}`}>
              <p className="prov">
                <span className="chip chip-ai">AI draft</span>{" "}
                Provider: <strong data-testid={`provider-${d.draft_id}`}>{d.provider}</strong>
                {d.fallback_used && <> · <span className="chip" data-testid={`fallback-${d.draft_id}`}>template fallback used</span></>}
                {d.purpose || d.channel ? <> · {PURPOSE_LABEL[d.purpose as DraftPurpose] ?? d.purpose}, {CHANNEL_LABEL[d.channel as DraftChannel] ?? d.channel}, {LANG_LABEL[d.language]}</> : null}
              </p>
              <p className="prov muted">
                Prompt hash{" "}
                {d.prompt_hash
                  ? <code className="mono" title={d.prompt_hash} data-testid={`hash-${d.draft_id}`}>{d.prompt_hash.slice(0, 12)}</code>
                  : "unavailable"}
                {" "}· generated {fmtDateTime(d.created_at)} by <ActorTag actor={d.created_by} />
              </p>
              {d.abstention_reason && (
                <p className="banner banner-warn" data-testid={`abstention-${d.draft_id}`} title={d.abstention_reason}>
                  <strong>AI abstained:</strong> {abstentionText(d.abstention_reason)}. The approved template was used instead.
                </p>
              )}
              {d.text ? (
                <div className="draft-text">
                  <p lang={d.language} dir={dir}>{d.text}</p>
                </div>
              ) : (
                <p className="muted">Draft text unavailable. Request a new draft.</p>
              )}

              {d.status === "approved" && (
                <p>
                  <span className="chip chip-ok" data-testid={`approved-${d.draft_id}`}>Approved by {actorName(d.approved_by ?? "") || d.approved_by} · {fmtDateTime(d.approved_at)}</span>{" "}
                  {d.sent_at || outcome?.sent === true ? (
                    <span className="chip chip-ok" data-testid={`sent-${d.draft_id}`}>Sent{d.sent_at ? ` ${fmtDateTime(d.sent_at)}` : ""}</span>
                  ) : blocked ? (
                    <span className="chip chip-warn" data-testid={`blocked-${d.draft_id}`}>Approved but blocked: {blocked}</span>
                  ) : (
                    <span className="chip" data-testid={`unsent-${d.draft_id}`}>Approved, no message sent yet</span>
                  )}
                  {blocked && <span className="muted"> Nothing was sent: {BLOCKED_TEXT[blocked] ?? blocked}.</span>}
                </p>
              )}
              {d.status === "rejected" && (
                <p>
                  <span className="chip chip-bad" data-testid={`rejected-${d.draft_id}`}>Rejected by {actorName(d.rejected_by ?? "") || d.rejected_by}: {d.rejection_reason}</span>
                  <span className="muted"> {d.rejected_at ? fmtDateTime(d.rejected_at) : ""} Nothing was sent.</span>
                </p>
              )}
              {d.status === "pending" && (
                <div className="decision">
                  <p className="muted">Waiting for the case owner to approve or reject.</p>
                  <button
                    type="button"
                    className="btn btn-primary"
                    data-testid={`approve-${d.draft_id}`}
                    disabled={!canDecide || !!approveWhy || busy}
                    aria-describedby={!canDecide ? noteId : undefined}
                    onClick={() => onApprove(d.draft_id)}
                  >
                    Approve draft
                  </button>
                  {canDecide && approveWhy && <span className="why" data-testid={`approve-${d.draft_id}-why`}>{approveWhy}</span>}
                  <label className="field"><span>Reason for rejecting</span>
                    <input value={reason} disabled={!canDecide} onChange={(e) => setReasons((r) => ({ ...r, [d.draft_id]: e.target.value }))} maxLength={500} />
                  </label>
                  <button
                    type="button"
                    className="btn"
                    data-testid={`reject-${d.draft_id}`}
                    disabled={!canDecide || !reason.trim() || busy}
                    aria-describedby={!canDecide ? noteId : undefined}
                    onClick={() => onReject(d.draft_id, reason.trim())}
                  >
                    Reject draft
                  </button>
                  {canDecide && !reason.trim() && <span className="why" data-testid={`reject-${d.draft_id}-why`}>Enter the reason for rejecting this draft first.</span>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
