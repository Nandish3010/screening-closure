import type { CaseEffort } from "../api/contract";
import { STAFF } from "../i18n/strings";

const pct = (x: number | null | undefined) => (x === null || x === undefined ? null : `${Math.round(x * 100)}%`);

/**
 * "Human actions N / Automated actions M" for one case (A21), with the automation share and the estimated minutes saved.
 * The minutes are assumed, not measured, and the line says so.
 */
export function EffortCounter({ effort }: { effort: CaseEffort }) {
  const share = pct(effort.automation_share);
  return (
    <p className="effort-counter" data-testid="effort-counter" aria-label={STAFF.effort.label}>
      <span data-testid="effort-human"><span className="effort-label">{STAFF.effort.human}</span> <strong>{effort.human_actions}</strong></span>
      <span aria-hidden="true"> / </span>
      <span data-testid="effort-automated"><span className="effort-label">{STAFF.effort.automated}</span> <strong>{effort.automated_actions}</strong></span>
      {share && <span className="muted" data-testid="effort-share"> · {STAFF.effort.share} {share}</span>}
      {effort.minutes_saved > 0 && (
        <span className="muted" data-testid="effort-saved"> · {STAFF.effort.saved} {effort.minutes_saved} <span className="effort-assumed">({STAFF.effort.assumed})</span></span>
      )}
    </p>
  );
}
