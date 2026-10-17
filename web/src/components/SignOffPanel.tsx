import { useState } from "react";
import type { Role } from "../api/contract";
import { ActionButton } from "./ActionButton";

/**
 * Clinician sign-off. The Close button stays disabled until BOTH an evidence reference and a
 * signer are present (and the acting role is clinician), and it says why while disabled.
 * The engine enforces the same rule (state_machine.apply) - this is the UI mirror, not the guard.
 */
export function SignOffPanel({
  role, onSignOff, busy, needsReview,
}: {
  role: Role;
  onSignOff: (evidenceRef: string, signedBy: string) => void;
  busy?: boolean;
  /** True when the case is still at confirmatory_completed (review event is posted first). */
  needsReview?: boolean;
}) {
  const [evidence, setEvidence] = useState("");
  const [signer, setSigner] = useState("");

  const reasons: string[] = [];
  if (role !== "clinician") reasons.push("Only a clinician can sign off. Switch the role to clinician.");
  if (!evidence.trim()) reasons.push("Add an evidence reference (for example the colonoscopy report id).");
  if (!signer.trim()) reasons.push("Enter the name of the clinician who signs.");

  return (
    <fieldset className="panel signoff">
      <legend>Clinician sign-off and closure</legend>
      <p className="muted">
        A case closes only with evidence and a named human sign-off. Nothing automated can close a case.
        {needsReview && " Signing off also records the clinician review."}
      </p>
      <label className="field">
        <span>Evidence reference</span>
        <input value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="DiagnosticReport/colonoscopy-0001" />
      </label>
      <label className="field">
        <span>Signed by</span>
        <input value={signer} onChange={(e) => setSigner(e.target.value)} placeholder="Dr. Huda Rahman" />
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
