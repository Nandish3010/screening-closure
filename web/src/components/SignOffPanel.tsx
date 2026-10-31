import { useEffect, useState } from "react";
import type { Role } from "../api/contract";
import { can } from "../app/AppContext";
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
export function SignOffPanel({
  role, onSignOff, busy, needsReview, extraWhy, defaultEvidence, signer: fixedSigner, onRecordResult, procedure = "colonoscopy",
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
  if (!can(role, "signOff")) reasons.push("Only a clinician (a case owner) can sign off. Switch the role to owner, specialist or clinician.");
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
      setRecordError(`The engine refused this: ${(e as Error).message}`);
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
      <label className="field">
        <span>Evidence reference</span>
        <input value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder={`Procedure/${procedure}-0001`} />
      </label>
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
