import type { CaseInsurance } from "../api/contract";
import { INSURANCE_NOTE } from "../lib/insurance";

/**
 * A case's insurance plan and why its referral went where it did (ADR-056), for the case page's facts list. Staff only (a patient's
 * page carries no `insurance`). The reason is the server's plain words; an out-of-network referral is flagged for a coordinator and
 * is never blocked. Renders nothing for a case with no recorded scheme.
 */
export function InsuranceFact({ insurance }: { insurance?: CaseInsurance | null }) {
  if (!insurance) return null;
  const referral = insurance.referral;
  const inNetwork = insurance.providers.filter((p) => p.network === "in_network").map((p) => p.name);
  return (
    <div>
      <dt>Insurance</dt>
      <dd data-testid="insurance">
        <span data-testid="insurance-scheme">{insurance.scheme === "unknown" ? "Plan not known" : `${insurance.label} plan`}</span>
        {referral?.reason && (
          <div data-testid="referral-why">
            <strong>Why {referral.provider}: </strong>
            {referral.out_of_network && <span className="chip chip-warn" data-testid="out-of-network-chip">Out of network</span>}{" "}
            <span data-testid="referral-reason">{referral.reason}</span>
          </div>
        )}
        {referral?.out_of_network && inNetwork.length > 0 && (
          <div className="muted" data-testid="in-network-options">Units that accept this plan: {inNetwork.join(", ")}.</div>
        )}
        <div className="muted" data-testid="insurance-note">{INSURANCE_NOTE}</div>
      </dd>
    </div>
  );
}
