import type { EffortMetrics } from "../api/contract";
import { STAFF } from "../i18n/strings";

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`);

/**
 * "Human effort" card for the dashboard (A21, coordinator and supervisor): the reduction in human time against the programme
 * target, and the human versus automated actions behind it. The minutes are assumed, not measured; the caveat is always shown.
 */
export function EffortCard({ metrics }: { metrics: EffortMetrics | null }) {
  const s = STAFF.effortCard;
  const t = metrics?.totals;
  const reduction = t?.reduction_pct ?? null;
  return (
    <section className="panel effort-card" aria-labelledby="effort-h" data-testid="effort-card">
      <h2 id="effort-h">{s.heading}</h2>
      <p className="muted">{s.intro}</p>
      {!metrics || !t ? (
        <p className="muted" data-testid="effort-empty">{s.noData}</p>
      ) : (
        <>
          <div className="effort-headline">
            <span className="kpi-value" data-testid="effort-reduction">{reduction === null ? "n/a" : `${reduction}%`}</span>{" "}
            <span className="kpi-label">{s.reduction}</span>
            {" "}
            <span className={`chip ${t.meets_target ? "chip-ok" : "chip-warn"}`} data-testid="effort-target-status">
              {t.meets_target ? s.met : s.notMet}
            </span>
          </div>
          <div
            className="meter"
            role="meter"
            aria-label={`${s.reduction} against the ${t.target_pct}% target`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.max(0, Math.min(100, reduction ?? 0))}
            aria-valuetext={`${reduction ?? 0}% of ${t.target_pct}% target`}
          >
            <div className="meter-fill" style={{ width: `${Math.max(0, Math.min(100, reduction ?? 0))}%` }} />
            <div className="meter-target" style={{ left: `${t.target_pct}%` }} title={`${s.target} ${t.target_pct}%`} />
          </div>
          <p className="muted" data-testid="effort-target">{s.target}: {t.target_pct}%</p>
          <dl className="facts effort-facts">
            <div><dt>{s.humanActions}</dt><dd data-testid="effort-total-human">{t.human_actions}</dd></div>
            <div><dt>{s.automatedActions}</dt><dd data-testid="effort-total-automated">{t.automated_actions}</dd></div>
            <div><dt>{s.share}</dt><dd data-testid="effort-total-share">{pct(t.automation_share)}</dd></div>
            <div><dt>{s.minutes}</dt><dd>{t.human_minutes} / {t.baseline_minutes}</dd></div>
            <div><dt>{s.saved}</dt><dd data-testid="effort-total-saved">{t.minutes_saved}</dd></div>
          </dl>
        </>
      )}
      <p className="banner banner-warn" data-testid="effort-caveat">
        {s.caveat}
        {metrics?.assumptions.note ? ` ${metrics.assumptions.note}` : ""}
      </p>
      {metrics && (
        <details>
          <summary>Assumed minutes per action</summary>
          <ul className="plain" data-testid="effort-assumptions">
            {Object.entries(metrics.assumptions.minutes).map(([k, v]) => <li key={k}>{k.replace(/_/g, " ")}: {v} min</li>)}
          </ul>
        </details>
      )}
    </section>
  );
}
