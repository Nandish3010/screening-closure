import { useEffect, useState } from "react";
import { procedureSlug } from "../lib/pathway";
import type { Role } from "../api/contract";
import { can } from "../app/AppContext";
import { fmtDateTime, plainError, shortId } from "../lib/format";
import type { CaseEvent } from "../api/contract";
import { ActionButton } from "./ActionButton";

/**
 * Clinician sign-off. The Close button stays disabled until BOTH an evidence reference and a
 * signer are present (and the acting role is a clinician: an owner role), and it says why while disabled.
 * The engine enforces the same rule (state_machine.apply) - this is the UI mirror, not the guard.
 *
 * Against the real API (E8) the signer is the caller's own `Practitioner/<user_id>` (a signature is the signer's
 * own, A15), passed as `signer` and shown read-only, and the evidence must exist in the FHIR store: "Record
 * colonoscopy result" creates the Procedure there (POST /cases/{id}/confirmatory-result) and fills the field.
 */
export const MADE_UP_REF = "DiagnosticReport/made-up-0000";

/** What a closed case shows in place of the sign-off form: who signed, on what evidence, when. Read from the audit trail. */
export function ClosedReceipt({ events, caseId }: { events: CaseEvent[]; caseId: string }) {
  const close = [...events].reverse().find((e) => e.type === "close");
  if (!close) return null;
  const evidence = String(close.payload.evidence_ref ?? "");
  const signedBy = String(close.payload.signed_by ?? "");
  return (
    <section className="receipt" aria-labelledby="receipt-h" role="status" data-testid="closed-receipt">
      <h3 id="receipt-h"><span aria-hidden="true">{"\u2713"} </span>Case {shortId(caseId)} is closed</h3>
      <dl>
        <div><dt>Evidence</dt><dd className="mono">{evidence || "none recorded"}</dd></div>
        <div><dt>Signed by</dt><dd>{signedBy || "not recorded"}</dd></div>
        <div><dt>When</dt><dd>{fmtDateTime(close.at)}</dd></div>
      </dl>
      <p className="muted">The patient's app now shows this case as complete. The audit trail below is final unless a physician reopens it.</p>
    </section>
  );
}

export function SignOffPanel({
  role, onSignOff, busy, needsReview, extraWhy, defaultEvidence, signer: fixedSigner, onRecordResult, procedure = "colonoscopy", refusal, caseId, onClearRefusal,
}: {
  role: Role;
  /** A reason the acting clinician may not sign this case off, e.g. they do not own it (A5). */
  extraWhy?: string | null;
  onSignOff: (evidenceRef: string, signedBy: string) => void;
  busy?: boolean;
  /** True when the case is still at confirmatory_completed (review event is posted first). */
  needsReview?: boolean;
  /** Evidence already on the case (the confirmatory result's reference), offered as the starting value. */
  defaultEvidence?: string;
  /** The signer the API will accept (`Practitioner/<own user id>`); fixed and read-only. Without it the field is free text (mock). */
  signer?: string;
  /** Records the confirmatory procedure as FHIR evidence and returns its reference (owners only; absent = no button). */
  onRecordResult?: () => Promise<string>;
  /** The pathway's confirmatory procedure (colonoscopy, colposcopy): words the hint, the placeholder and the button. */
  procedure?: string;
  /** The engine's own words when it refused the close (shown as a designed refusal: nothing was closed). */
  refusal?: string | null;
  caseId?: string;
  onClearRefusal?: () => void;
}) {
  const [evidence, setEvidence] = useState(defaultEvidence ?? "");
  const [signerText, setSignerText] = useState("");
  const [recording, setRecording] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  const signer = fixedSigner ?? signerText;

  useEffect(() => {
    if (defaultEvidence) setEvidence((cur) => cur || defaultEvidence);
  }, [defaultEvidence]);

  const reasons: string[] = [];
  if (!can(role, "signOff")) reasons.push(`Only a physician who owns this case can sign off. You are viewing as the ${role}. Switch the role to owner, specialist or clinician in the header.`);
  else if (extraWhy) reasons.push(extraWhy);
  if (!evidence.trim()) reasons.push(`Add an evidence reference (for example the ${procedure} report id).`);
  if (!signer.trim()) reasons.push("Enter the name of the clinician who signs.");

  const record = async () => {
    if (!onRecordResult) return;
    setRecording(true);
    setRecordError(null);
    try {
      setEvidence(await onRecordResult());
    } catch (e) {
      setRecordError(`The engine refused this: ${plainError((e as Error).message)}`);
    } finally {
      setRecording(false);
    }
  };

  return (
    <fieldset className="panel signoff" id="signoff-panel" tabIndex={-1} data-testid="signoff-panel">
      <legend>Clinician sign-off and closure</legend>
      <p className="muted">
        A case closes only with evidence and a named human sign-off. Nothing automated can close a case.
        {needsReview && " Signing off also records the clinician review."}
      </p>
      {!can(role, "signOff") && (
        <p className="banner banner-warn role-denied" role="note" data-testid="signoff-role-denied">
          <strong>Read-only for your role.</strong> A physician who owns this case signs. You can check the evidence here; the button below stays off until a physician is signed in.
        </p>
      )}
      <label className="field">
        <span>Evidence reference</span>
        <input value={evidence} onChange={(e) => { setEvidence(e.target.value); onClearRefusal?.(); }} placeholder={`Procedure/${procedureSlug(procedure)}-0001`} aria-describedby="evidence-hint" autoComplete="off" spellCheck={false} />
      </label>
      <p id="evidence-hint" className="muted evidence-hint">Type a reference, or pick an example. The made-up one shows what the system does when a reference does not exist.</p>
      <div className="evidence-chips" role="group" aria-label="Example evidence references">
        <button type="button" className="chip-btn" data-testid="evidence-real" disabled={!defaultEvidence} onClick={() => { setEvidence(defaultEvidence ?? ""); onClearRefusal?.(); }}>
          <span className="chip chip-ok">Real</span> {defaultEvidence ? `The report on this case (${defaultEvidence})` : `No ${procedure} report on this case yet`}
        </button>
        <button type="button" className="chip-btn" data-testid="evidence-fake" onClick={() => { setEvidence(MADE_UP_REF); onClearRefusal?.(); }}>
          <span className="chip chip-warn">Made up</span> A reference that does not exist ({MADE_UP_REF})
        </button>
      </div>
      {onRecordResult && (
        <>
          <ActionButton testId="record-colonoscopy" busy={busy || recording} disabledReason={null} onClick={() => void record()}>
            Record {procedure} result
          </ActionButton>
          {recordError && <p className="banner banner-bad" role="alert">{recordError}</p>}
        </>
      )}
      <label className="field">
        <span>Signed by</span>
        {fixedSigner ? (
          <input value={fixedSigner} readOnly aria-readonly="true" data-testid="signer" />
        ) : (
          <input value={signerText} onChange={(e) => setSignerText(e.target.value)} placeholder="Dr. Huda Rahman" />
        )}
      </label>
      <section className="check-answers" aria-labelledby="check-h" data-testid="check-answers">
        <h3 id="check-h">Check your answers</h3>
        <dl>
          {caseId && <div><dt>Case</dt><dd className="mono">{shortId(caseId)}</dd></div>}
          <div><dt>Evidence</dt><dd className="mono" data-testid="check-evidence">{evidence.trim() || <span className="muted">Not entered yet</span>}</dd></div>
          <div><dt>Signed by</dt><dd data-testid="check-signer">{signer.trim() || <span className="muted">Not entered yet</span>}</dd></div>
          <div><dt>Result</dt><dd>Closes the case. The evidence and the signer's name go on the audit trail.</dd></div>
        </dl>
      </section>
      {refusal && (
        <div className="refusal-card" role="alert" data-testid="signoff-refusal">
          <h3>Nothing was closed</h3>
          <p>{refusal}</p>
          <p className="muted">The case is still open. Use the real report, or record the {procedure} result first, then sign again.</p>
        </div>
      )}
      <ActionButton
        primary
        testId="signoff"
        busy={busy}
        disabledReason={reasons.length ? reasons.join(" ") : null}
        onClick={() => onSignOff(evidence.trim(), signer.trim())}
      >
        Sign off and close case
      </ActionButton>
    </fieldset>
  );
}
