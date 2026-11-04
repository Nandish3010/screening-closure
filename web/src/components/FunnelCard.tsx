import { Link } from "react-router-dom";
import type { FunnelAtRiskCase, FunnelMetrics, FunnelStep } from "../api/contract";
import { STAFF } from "../i18n/strings";
import { UNRANKED, daysOf, pctOf } from "../lib/funnel";
import { shortId } from "../lib/format";
import { pathwayMeta } from "../lib/pathway";
import { priorityLabel } from "../lib/priority";

export interface FunnelFilters {
  /** A pack name, or "" for every pathway. */
  pathway: string;
  /** A tier, `unranked`, or "" for every case. */
  priority: string;
}
export const NO_FUNNEL_FILTER: FunnelFilters = { pathway: "", priority: "" };

/** Shown until the server has said which pathways and tiers it runs (its `options` replace these). */
const DEFAULT_OPTIONS = { pathways: ["colorectal_fit", "cervical_hpv", "chronic_conditions"], priorities: ["urgent", "soon", "routine", UNRANKED] };

const tierLabel = (tier: string) => (tier === UNRANKED ? STAFF.funnel.notRanked : priorityLabel({ tier }));

/** What the "median time" cell of a step says: nothing for the start, "at intake" for the tier, else the median and how many cases it is of. */
function MedianCell({ step }: { step: FunnelStep }) {
  const s = STAFF.funnel;
  if (step.key === "recorded") return <span className="muted">{s.start}</span>;
  if (step.key === "prioritised") return <span className="muted">{s.atIntake}</span>;
  return (
    <span>
      <span data-testid={`funnel-median-${step.key}`}>{daysOf(step.median_days)}</span>
      {step.median_n > 0 && <small className="funnel-n"> {s.casesN(step.median_n)}</small>}
    </span>
  );
}

function StepRow({ step, max }: { step: FunnelStep; max: number }) {
  const s = STAFF.funnel;
  const width = max ? Math.round((step.count / max) * 100) : 0;
  const of = step.from ? s.ofWord[step.from] : null;
  return (
    <tr data-testid={`funnel-row-${step.key}`}>
      <th scope="row">
        <span className="funnel-label">{step.label}</span>
        <span className="funnel-def muted">{step.definition}</span>
        {/* The bar is a picture of the count in the next cell: the number is the content, so the bar is hidden from assistive technology. */}
        <span className="funnel-bar" aria-hidden="true"><span style={{ width: `${width}%` }} /></span>
      </th>
      <td className="num" data-label={STAFF.funnel.colCases} data-testid={`funnel-count-${step.key}`}>{step.count}</td>
      <td className="num" data-label={s.colConversion}>
        {step.from === null ? <span className="muted">n/a</span> : (
          <span>
            <span data-testid={`funnel-conversion-${step.key}`}>{pctOf(step.conversion)}</span>
            {of && <small className="funnel-n"> {of}</small>}
          </span>
        )}
      </td>
      <td className="num" data-label={s.colMedian}><MedianCell step={step} /></td>
    </tr>
  );
}

function AtRiskItem({ c }: { c: FunnelAtRiskCase }) {
  const s = STAFF.funnel;
  return (
    <li data-testid="funnel-risk-row">
      <Link to={`/coordinator/${c.case_id}`}>{shortId(c.case_id)}</Link>{" "}
      <span className="chip chip-pathway">{pathwayMeta(c.pathway).label}</span>{" "}
      {c.priority && <span className={`chip chip-priority-${c.priority}`}>{tierLabel(c.priority)}</span>}{" "}
      <span className="muted">{c.reason === "contact_lost" ? s.riskContactLost(c.idle_days) : s.riskIdle(c.idle_days)}</span>
    </li>
  );
}

/**
 * The outcome funnel (A39, ADR-052) for the dashboard: the programme owner's five-step loop as a real table (count, share of the
 * previous step, median time from it), a pathway and a priority filter, and the cases at risk of being lost. Everything is
 * synthetic and on the simulated clock; it counts cases that reached a step and claims nothing about health outcomes.
 */
export function FunnelCard({
  data, filters, onFilters, error, loading,
}: {
  data: FunnelMetrics | null;
  filters: FunnelFilters;
  onFilters: (f: FunnelFilters) => void;
  error: string | null;
  loading: boolean;
}) {
  const s = STAFF.funnel;
  const options = data?.options ?? DEFAULT_OPTIONS;
  const max = data ? Math.max(0, ...data.steps.map((x) => x.count)) : 0;
  return (
    <section className="panel funnel-card" aria-labelledby="funnel-h" aria-busy={loading} data-testid="funnel-card">
      <div className="page-head">
        <h2 id="funnel-h">{s.heading}</h2>
        <span className="chip chip-synthetic" data-testid="funnel-synthetic">Synthetic data</span>
      </div>
      <p className="muted">{s.intro} {s.synthetic}</p>
      <form className="filters" aria-label={s.filtersLabel} onSubmit={(e) => e.preventDefault()}>
        <label>
          {s.pathway}
          <select value={filters.pathway} data-testid="funnel-pathway" onChange={(e) => onFilters({ ...filters, pathway: e.target.value })}>
            <option value="">{s.allPathways}</option>
            {options.pathways.map((p) => <option key={p} value={p}>{pathwayMeta(p).label}</option>)}
          </select>
        </label>
        <label>
          {s.priority}
          <select value={filters.priority} data-testid="funnel-priority" onChange={(e) => onFilters({ ...filters, priority: e.target.value })}>
            <option value="">{s.allPriorities}</option>
            {options.priorities.map((t) => <option key={t} value={t}>{tierLabel(t)}</option>)}
          </select>
        </label>
      </form>
      {error && <p className="banner banner-bad" role="alert" data-testid="funnel-error">{s.error(error)}</p>}
      {!data && !error && <p role="status">{s.loading}</p>}
      {data && (
        <>
          <div className="table-wrap">
            <table className="funnel-table" data-testid="funnel-table">
              <caption className="visually-hidden">{s.caption}</caption>
              <thead>
                <tr>
                  <th scope="col">{s.colStep}</th>
                  <th scope="col" className="num">{s.colCases}</th>
                  <th scope="col" className="num">{s.colConversion}</th>
                  <th scope="col" className="num">{s.colMedian}</th>
                </tr>
              </thead>
              <tbody>{data.steps.map((step) => <StepRow key={step.key} step={step} max={max} />)}</tbody>
            </table>
          </div>
          {data.declined_closures > 0 && <p className="muted" data-testid="funnel-declined">{s.declined(data.declined_closures)}</p>}
          {data.replay_failures.count > 0 && <p className="banner banner-warn" role="status" data-testid="funnel-failures">{s.failures(data.replay_failures.count)}</p>}
          <p className="muted funnel-note" data-testid="funnel-note">{s.note}</p>

          <div className="funnel-risk" data-testid="funnel-at-risk">
            <h3>{s.riskHeading}</h3>
            <p className="funnel-risk-count">
              <span className="kpi-value" data-testid="funnel-risk-count">{data.at_risk.count}</span>{" "}
              <span className="kpi-label">{s.riskCountLabel(data.at_risk.count)}</span>
            </p>
            <p className="muted">{s.riskDefinition(data.at_risk.idle_days_threshold)}</p>
            {data.at_risk.count === 0 ? (
              <p className="banner banner-ok" data-testid="funnel-risk-empty">{s.riskNone}</p>
            ) : (
              <>
                <ul className="plain funnel-risk-list" data-testid="funnel-risk-list">
                  {data.at_risk.cases.map((c) => <AtRiskItem key={c.case_id} c={c} />)}
                </ul>
                {data.at_risk.truncated && <p className="muted">{s.riskShowing(data.at_risk.cases.length, data.at_risk.count)}</p>}
              </>
            )}
            <p><Link to="/coordinator" className="btn btn-small" data-testid="funnel-worklist-link">{s.riskWorklist}</Link></p>
          </div>
        </>
      )}
    </section>
  );
}
