import type { EffortMetrics } from "../api/contract";
import { STAFF } from "../i18n/strings";

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`);


/**
 * Two small pictures of the same numbers the facts list below spells out: who did the actions (solid = a person, hatched = the
 * system) and the minutes against the baseline (solid = with the system, outlined = by hand). Each bar prints its figure, so the
 * pictures never rely on colour, and the group carries the same figures as its text alternative.
 */
function EffortChart({ t }: { t: NonNullable<EffortMetrics["totals"]> }) {
  const actions = t.human_actions + t.automated_actions;
  const humanPct = actions ? (t.human_actions / actions) * 100 : 0;
  const base = Math.max(1, t.baseline_minutes, t.human_minutes);
  const alt = `${t.human_actions} actions by people and ${t.automated_actions} automated. ${t.human_minutes} human minutes against ${t.baseline_minutes} baseline minutes. Demo cases, assumed minutes.`;
  return (
    <figure className="effort-chart" role="img" aria-label={alt} data-testid="effort-chart">
      <figcaption aria-hidden="true">Who did the actions</figcaption>
      <div className="effort-stack" aria-hidden="true">
        {t.human_actions > 0 && <span className="effort-seg-human" style={{ width: `${humanPct}%` }}>People {t.human_actions}</span>}
        {t.automated_actions > 0 && <span className="effort-seg-auto" style={{ width: `${100 - humanPct}%` }}>System {t.automated_actions}</span>}
      </div>
      <div className="effort-rows" aria-hidden="true">
        <div className="effort-row">
          <span className="effort-row-label">Minutes with the system</span>
          <span className="effort-bar effort-bar-human"><i style={{ width: `${(t.human_minutes / base) * 70}%` }} /><span className="effort-bar-num">{t.human_minutes}</span></span>
        </div>
        <div className="effort-row">
          <span className="effort-row-label">Minutes by hand (baseline)</span>
          <span className="effort-bar effort-bar-base"><i style={{ width: `${(t.baseline_minutes / base) * 70}%` }} /><span className="effort-bar-num">{t.baseline_minutes}</span></span>
        </div>
      </div>
    </figure>
  );
}

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
            {/* Never a "Target met" verdict here: this is a handful of demo cases with assumed minutes, not a programme result. */}
            <span className="chip chip-neutral" data-testid="effort-target-status">
              {reduction === null ? s.notEnoughData : s.sampleLabel}
            </span>
          </div>
          {/* No meter and no target marker: a bar that fills past a 40% tick would read as "met" for what is only a demo sample. */}
          <p className="muted" data-testid="effort-target">{s.target}: {t.target_pct}% (the programme target; see the estimate below)</p>
          <p className="muted" data-testid="effort-programme">{s.programme}</p>
          <EffortChart t={t} />
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
