import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { CaseSummary } from "../api/contract";
import { MAIN_STATES } from "../api/contract";
import { DEFAULT_FILTERS, UNOWNED, ownersOf, worklist, type WorklistFilters } from "../lib/worklist";
import { fmtDate, fmtDateTime, shortId, titleCase } from "../lib/format";
import { overdueInfo } from "../lib/derive";
import { WatchdogMarker } from "../components/WatchdogMarker";

export function StatePill({ state }: { state: string }) {
  return <span className={`pill pill-${state}`}>{titleCase(state)}</span>;
}

export function CoordinatorPage() {
  const { api, simTime } = useApp();
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<WorklistFilters>(DEFAULT_FILTERS);
  const [reloads, setReloads] = useState(0);
  const [filed, setFiled] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.listCases().then((c) => live && setCases(c)).catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [api, simTime, reloads]);

  // Demo only (mock adapter): file a positive result and watch the automation actor acknowledge and assign it.
  const fileResult = () =>
    api.createCase!()
      .then((id) => { setFiled(id); setReloads((n) => n + 1); })
      .catch((e) => setError(String(e.message ?? e)));

  // "Now" for overdue days: the sim clock; if it is unavailable, the newest event on any case.
  const now = useMemo(
    () => simTime ?? (cases ?? []).reduce<string | null>((m, c) => (m === null || c.last_event_at > m ? c.last_event_at : m), null),
    [simTime, cases],
  );
  const rows = useMemo(() => (cases ? worklist(cases, filters, now) : []), [cases, filters, now]);
  const owners = useMemo(() => ownersOf(cases ?? []), [cases]);
  const set = (p: Partial<WorklistFilters>) => setFilters((f) => ({ ...f, ...p }));

  return (
    <main className="container">
      <h1>Worklist</h1>
      <p className="muted">Most overdue first, then highest risk of loss to follow-up. The risk score only orders this list; it never closes, escalates or decides care.</p>

      {api.createCase && (
        <p className="muted">
          Demo only (mock adapter):{" "}
          <button type="button" className="btn btn-small" data-testid="file-result" onClick={fileResult}>File a new positive result</button>
          {filed && (
            <span role="status" data-testid="filed-note"> Filed <Link to={`/coordinator/${filed}`}>{shortId(filed)}</Link>{api.automation ? ": the system acknowledged it and assigned an owner." : ": it waits to be acknowledged."}</span>
          )}
        </p>
      )}

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
        <label className="check">
          <input type="checkbox" checked={filters.flaggedOnly} onChange={(e) => set({ flaggedOnly: e.target.checked })} />
          <span>Flagged only (nobody is looking)</span>
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
                <tr><th>Case</th><th>State</th><th>Owner</th><th>Due</th><th>Overdue</th><th>Escalation</th><th>Flags</th><th>Last activity</th><th>Risk</th></tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const od = overdueInfo(c, now);
                  return (
                    <tr key={c.case_id} data-testid="worklist-row" className={`${od.overdue ? "row-overdue" : ""} ${c.watchdog_flagged ? "row-watchdog" : ""}`.trim()}>
                      <td data-label="Case"><Link to={`/coordinator/${c.case_id}`}>{shortId(c.case_id)}</Link></td>
                      <td data-label="State"><StatePill state={c.state} /></td>
                      <td data-label="Owner">{c.owner ?? <span className="muted">Unowned</span>}{c.provider && <span className="muted"> · {c.provider}</span>}</td>
                      <td data-label="Due">{fmtDate(c.due_at)}</td>
                      <td data-label="Overdue">{od.overdue ? <span className="chip chip-bad">{od.days > 0 ? `${od.days} day${od.days === 1 ? "" : "s"}` : "overdue"}</span> : <span className="muted">-</span>}</td>
                      <td data-label="Escalation">{c.escalation_level > 0 ? <span className="chip chip-warn" title={`Escalation level ${c.escalation_level} of 3`}>Level {c.escalation_level}</span> : <span className="muted">-</span>}</td>
                      <td data-label="Flags">
                        {c.watchdog_flagged && <WatchdogMarker flag={c.watchdog_flagged} compact />}
                        {c.consent_frozen && <span className="chip chip-warn" title="Patient consent is frozen: do not contact the patient">Consent frozen</span>}
                        {c.pending_handoff && <span className="chip chip-warn" data-testid="pending-handoff-chip" title={`Hand-off to ${c.pending_handoff.to_provider} is waiting for acceptance`}>Hand-off pending</span>}
                        {c.last_handoff?.bounced && c.state === "referral_rejected" && <span className="chip chip-bad" data-testid="bounced-chip" title="The provider did not answer in 3 working days">Hand-off bounced</span>}
                        {!c.watchdog_flagged && !c.consent_frozen && !c.pending_handoff && !(c.last_handoff?.bounced && c.state === "referral_rejected") && <span className="muted">-</span>}
                      </td>
                      <td data-label="Last activity">{fmtDateTime(c.last_event_at)}</td>
                      <td data-label="Risk"><RiskBar value={c.risk_score} /></td>
                    </tr>
                  );
                })}
                {rows.length === 0 && <tr><td colSpan={9}>No cases match these filters.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </main>
  );
}

export function RiskBar({ value }: { value?: number | null }) {
  if (value === undefined || value === null) return <span className="muted">-</span>;
  return (
    <span className="risk" title="Loss-to-follow-up risk (orders the list only)">
      <span className="risk-bar" aria-hidden="true"><span style={{ width: `${Math.round(value * 100)}%` }} /></span>
      <span>{value.toFixed(2)}</span>
    </span>
  );
}
