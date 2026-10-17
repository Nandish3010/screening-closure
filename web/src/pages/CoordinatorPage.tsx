import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { CaseSummary } from "../api/contract";
import { MAIN_STATES } from "../api/contract";
import { DEFAULT_FILTERS, UNOWNED, ownersOf, worklist, type WorklistFilters } from "../lib/worklist";
import { fmtDate, shortId, titleCase } from "../lib/format";

export function StatePill({ state }: { state: string }) {
  return <span className={`pill pill-${state}`}>{titleCase(state)}</span>;
}

export function CoordinatorPage() {
  const { api, simTime } = useApp();
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<WorklistFilters>(DEFAULT_FILTERS);

  useEffect(() => {
    let live = true;
    api.listCases().then((c) => live && setCases(c)).catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [api, simTime]);

  const rows = useMemo(() => (cases ? worklist(cases, filters) : []), [cases, filters]);
  const owners = useMemo(() => ownersOf(cases ?? []), [cases]);
  const set = (p: Partial<WorklistFilters>) => setFilters((f) => ({ ...f, ...p }));

  return (
    <main className="container">
      <h1>Worklist</h1>
      <p className="muted">Most overdue first, then highest risk of loss to follow-up. The risk score only orders this list; it never closes, escalates or decides care.</p>

      <form className="filters" aria-label="Worklist filters" onSubmit={(e) => e.preventDefault()}>
        <label className="field">
          <span>State</span>
          <select value={filters.state} onChange={(e) => set({ state: e.target.value })}>
            <option value="open">All open</option>
            <option value="all">All, including closed</option>
            {MAIN_STATES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Owner</span>
          <select value={filters.owner} onChange={(e) => set({ owner: e.target.value })}>
            <option value="">Anyone</option>
            <option value={UNOWNED}>Unowned</option>
            {owners.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={filters.overdueOnly} onChange={(e) => set({ overdueOnly: e.target.checked })} />
          <span>Overdue only</span>
        </label>
      </form>

      {error && <p className="banner banner-bad" role="alert">Could not load cases: {error}</p>}
      {!cases && !error && <p role="status">Loading cases...</p>}
      {cases && (
        <>
          <p className="muted" role="status">{rows.length} of {cases.length} cases</p>
          <div className="table-wrap">
            <table className="worklist" data-testid="worklist">
              <thead>
                <tr><th>Case</th><th>State</th><th>Owner</th><th>SLA due</th><th>Overdue</th><th>Risk</th></tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const od = c.overdue_days ?? 0;
                  return (
                    <tr key={c.case_id} data-testid="worklist-row" className={od > 0 ? "row-overdue" : ""}>
                      <td data-label="Case"><Link to={`/coordinator/${c.case_id}`}>{shortId(c.case_id)}</Link></td>
                      <td data-label="State"><StatePill state={c.state} /></td>
                      <td data-label="Owner">{c.owner ?? <span className="muted">Unowned</span>}</td>
                      <td data-label="SLA due">{fmtDate(c.sla_due_at)}</td>
                      <td data-label="Overdue">{od > 0 ? <span className="chip chip-bad">{od} day{od === 1 ? "" : "s"}</span> : <span className="muted">-</span>}</td>
                      <td data-label="Risk"><RiskBar value={c.risk_score} /></td>
                    </tr>
                  );
                })}
                {rows.length === 0 && <tr><td colSpan={6}>No cases match these filters.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </main>
  );
}

export function RiskBar({ value }: { value?: number }) {
  if (value === undefined) return <span className="muted">-</span>;
  return (
    <span className="risk" title="Loss-to-follow-up risk (orders the list only)">
      <span className="risk-bar" aria-hidden="true"><span style={{ width: `${Math.round(value * 100)}%` }} /></span>
      <span>{value.toFixed(2)}</span>
    </span>
  );
}
