import type { ClosureProposal } from "../api/contract";
import { STAFF } from "../i18n/strings";
import { fmtDateTime } from "../lib/format";

/**
 * The closure-proposed banner (A21). The system found the colonoscopy evidence and proposes closing the case; closing is still
 * a clinician's click. `onReview` (clinician who owns the case only) jumps to the sign-off panel, whose evidence field is
 * already filled with the proposed reference. Without it the banner is informational and says who can sign off.
 */
export function ClosureProposalBanner({ proposal, onReview, whyNot }: { proposal: ClosureProposal; onReview?: () => void; whyNot?: string | null }) {
  const s = STAFF.closureBanner;
  return (
    <section className="banner banner-ok closure-proposal" aria-labelledby="closure-proposal-h" data-testid="closure-proposal-banner">
      <h3 id="closure-proposal-h">{s.heading}</h3>
      <p>
        {s.found} <code data-testid="closure-proposal-evidence">{proposal.evidence_ref}</code>{" "}
        <span className="muted">(proposed {fmtDateTime(proposal.proposed_at)})</span>
      </p>
      <p className="muted">{s.humanStep}</p>
      {onReview ? (
        <>
          <button type="button" className="btn btn-primary" onClick={onReview} data-testid="closure-proposal-review" aria-describedby="closure-proposal-hint">
            {s.button}
          </button>
          <span id="closure-proposal-hint" className="visually-hidden">{s.buttonHint}</span>
        </>
      ) : (
        <p className="why" data-testid="closure-proposal-why">{whyNot ?? s.notClinician}</p>
      )}
    </section>
  );
}
