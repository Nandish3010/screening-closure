import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { CaseSummary } from "../api/contract";
import { MAIN_STATES } from "../api/contract";
import { DEFAULT_FILTERS, UNOWNED, ownersOf, pathwaysOf, worklist, type WorklistFilters } from "../lib/worklist";
import { pathwayMeta } from "../lib/pathway";
import { PathwayBadge } from "../components/PathwayBadge";
import { fmtDate, fmtDateTime, shortId, titleCase } from "../lib/format";
import { overdueInfo } from "../lib/derive";
import { WatchdogMarker } from "../components/WatchdogMarker";
import { WhyPanel } from "../components/WhyPanel";

/** Whole days from the case's creation to "now" (the sim clock); null when either is unknown. */
function ageDays(created: string | null | undefined, now: string | null): number | null {
  if (!created || !now) return null;
  const d = Math.floor((new Date(now).getTime() - new Date(created).getTime()) / 86_400_000);
  return Number.isFinite(d) ? Math.max(0, d) : null;
}

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
  const pathways = useMemo(() => pathwaysOf(cases ?? []), [cases]);
  const set = (p: Partial<WorklistFilters>) => setFilters((f) => ({ ...f, ...p }));

  return (
    <main className="container">
      <div className="page-head"><h1>Worklist</h1><span className="chip chip-synthetic">Synthetic data</span></div>
      <p className="muted">Most overdue first, then highest risk of loss to follow-up. The risk score only orders this list; it never closes, escalates or decides care.</p>
      <WhyPanel testId="why-risk" subject="risk order" input={{ kind: "risk" }} />

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
        {pathways.length > 1 && (
          <label className="field">
            <span>Pathway</span>
            <select value={filters.pathway} onChange={(e) => set({ pathway: e.target.value })}>
              <option value="">Every pathway</option>
              {pathways.map((p) => <option key={p} value={p}>{pathwayMeta(p).label}</option>)}
            </select>
          </label>
        )}
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
          <ul className="plain summary-chips" aria-label="At a glance" data-testid="worklist-summary">
            <li><span className="chip chip-bad chip-overdue">{rows.filter((c) => overdueInfo(c, now).overdue).length} overdue</span></li>
            <li><span className="chip chip-warn chip-escalated">{rows.filter((c) => c.escalation_level > 0).length} escalated</span></li>
            <li><span className="chip chip-watchdog">{rows.filter((c) => c.watchdog_flagged).length} with nobody looking</span></li>
            <li><span className="chip chip-held">{rows.filter((c) => c.consent_frozen || c.pending_handoff).length} held</span></li>
          </ul>
          <div className="table-wrap">
            <table className="worklist" data-testid="worklist">
              <thead>
                <tr><th>Case</th><th>Pathway</th><th>Age</th><th>State</th><th>Owner</th><th>Next due</th><th>Flags</th><th>Last activity</th><th>Risk</th></tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const od = overdueInfo(c, now);
                  const age = ageDays(c.created_at, now);
                  const bouncedNow = !!(c.last_handoff?.bounced && c.state === "referral_rejected");
                  const noFlags = !od.overdue && c.escalation_level === 0 && !c.watchdog_flagged && !c.consent_frozen && !c.pending_handoff && !bouncedNow && c.state !== "declined_pending_signoff";
                  return (
                    <tr key={c.case_id} data-testid="worklist-row" className={`${od.overdue ? "row-overdue" : ""} ${c.watchdog_flagged ? "row-watchdog" : ""}`.trim()}>
                      <td data-label="Case" className="nowrap"><Link to={`/coordinator/${c.case_id}`}>{shortId(c.case_id)}</Link></td>
                      <td data-label="Pathway" className="nowrap"><PathwayBadge pathway={c.pathway} testId="worklist-pathway" /></td>
                      <td data-label="Age" className="nowrap num" title="Days since the result was received">{age === null ? "-" : `${age} d`}</td>
                      <td data-label="State"><StatePill state={c.state} /></td>
                      <td data-label="Owner">
                        {c.owner ? <span className="owner-name">{c.owner}</span> : <span className="muted">Unowned</span>}
                        {c.provider && <span className="owner-sub">{c.provider}</span>}
                      </td>
                      <td data-label="Next due" className="nowrap num">{fmtDate(c.due_at)}</td>
                      <td data-label="Flags">
                        <span className="flags">
                          {od.overdue && <span className="chip chip-bad chip-overdue">{od.days > 0 ? `Overdue: ${od.days} ${od.days === 1 ? "day" : "days"}` : "Overdue"}</span>}
                          {c.escalation_level > 0 && <span className="chip chip-warn chip-escalated" title={`Escalation level ${c.escalation_level} of 3`}>Escalated: Level {c.escalation_level}</span>}
                          {c.watchdog_flagged && <WatchdogMarker flag={c.watchdog_flagged} compact />}
                          {c.consent_frozen && <span className="chip chip-held" title="Patient consent is frozen: do not contact the patient">Consent frozen</span>}
                          {c.pending_handoff && <span className="chip chip-held" data-testid="pending-handoff-chip" title={`Hand-off to ${c.pending_handoff.to_provider} is waiting for acceptance`}>Hand-off pending</span>}
                          {bouncedNow && <span className="chip chip-bad" data-testid="bounced-chip" title="The provider did not answer in 3 working days">Hand-off bounced</span>}
                          {c.state === "declined_pending_signoff" && <span className="chip chip-declined" title="The patient declined after counselling; a physician signs the closure">Declined by patient</span>}
                          {noFlags && <span className="muted">-</span>}
                        </span>
                      </td>
                      <td data-label="Last activity" className="nowrap num">{fmtDateTime(c.last_event_at)}</td>
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
