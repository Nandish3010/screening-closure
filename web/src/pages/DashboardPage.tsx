import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { AgeBucket, Alert, CaseSummary, ClosureMetrics, EffortMetrics } from "../api/contract";
import { BarChart } from "../components/BarChart";
import { EffortCard } from "../components/EffortCard";
import { OutreachDemoBanner } from "../components/OutreachDemoBanner";
import { WatchdogMarker, watchdogDetail } from "../components/WatchdogMarker";
import { fmtDateTime, shortId, titleCase } from "../lib/format";
import { StatePill } from "./CoordinatorPage";

const BUCKETS: AgeBucket[] = ["0-30", "31-60", "61-90", "90+"];

/** How often the dashboard re-reads the alerts: the stale-scheduler banner must appear within 60 s (T-C16). */
export const ALERT_POLL_MS = 10_000;

/** A time to closure in days, or "n/a" while nothing has closed yet (the API sends null then). */
function Days({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <>n/a</>;
  return <>{value}<small> days</small></>;
}

export function DashboardPage() {
  const { api, role } = useApp();
  const [effort, setEffort] = useState<EffortMetrics | null>(null);
  const [m, setM] = useState<ClosureMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flagged, setFlagged] = useState<CaseSummary[] | null>(null);
  const [flaggedError, setFlaggedError] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    let live = true;
    setM(null);
    setError(null); // a 403 for the previous role must not outlive a role switch
    // One tick first (see the effort effect below): the provider logs in as the chosen role after this effect in the same commit.
    Promise.resolve().then(() => api.closureMetrics()).then((x) => live && setM(x)).catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [api, role]);

  // A21: the human-effort card is for the coordinator and the supervisor (the API refuses the other roles).
  const seesEffort = role === "coordinator" || role === "supervisor";
  useEffect(() => {
    let live = true;
    setEffort(null);
    // One tick first: this effect runs before the provider's login effect in the same commit (children first), so a role switch on
    // this page would otherwise ask with the previous role's token and get a 403, which the card shows as "No effort data yet".
    if (seesEffort) Promise.resolve().then(() => api.getEffortMetrics()).then((x) => live && setEffort(x)).catch(() => live && setEffort(null));
    return () => {
      live = false;
    };
  }, [api, seesEffort, role]);

  // "Cases nobody is looking at": the watchdog's open flags, longest idle first.
  const loadFlagged = useCallback(() => {
    api.listCases({ flagged: true })
      .then((rows) => {
        setFlagged(rows.filter((c) => c.watchdog_flagged && c.state !== "closed").sort((a, b) => (b.watchdog_flagged?.idle_days ?? 0) - (a.watchdog_flagged?.idle_days ?? 0)));
        setFlaggedError(null);
      })
      .catch((e) => setFlaggedError(String(e.message ?? e)));
  }, [api]);
  useEffect(loadFlagged, [loadFlagged]);

  const loadAlerts = useCallback(() => {
    api.listAlerts().then(setAlerts).catch(() => undefined); // an unreachable alert feed must not hide the dashboard
  }, [api]);
  useEffect(() => {
    loadAlerts();
    const t = setInterval(loadAlerts, ALERT_POLL_MS);
    return () => clearInterval(t);
  }, [loadAlerts]);

  const down = alerts.filter((a) => a.kind === "scheduler_down" && !a.cleared_at);
  const recovered = alerts.filter((a) => a.kind === "scheduler_down" && a.cleared_at);

  return (
    <main className="container">
      <div className="page-head"><h1>Closure dashboard</h1><span className="chip chip-synthetic" data-testid="synthetic-chip">Synthetic data</span></div>
      <OutreachDemoBanner />
      <p className="muted" data-testid="dashboard-subtitle">
        {api.adapter === "http"
          ? "Synthetic data on a simulated clock. Live data from the API."
          : "Synthetic data on a simulated clock. Fixture data: every number is regenerated from a fixed seed."}
      </p>
      {down.map((a) => (
        <p key={a.since} className="banner banner-watchdog" role="alert" data-testid="scheduler-banner">
          <strong>The scheduler is down.</strong> It has not reported since {fmtDateTime(a.since)}, so SLA clocks are not being checked and no case will be marked overdue or escalated until it is back.
        </p>
      ))}
      {down.length === 0 && recovered.length > 0 && (
        <p className="banner banner-ok" role="status" data-testid="scheduler-recovered">
          The scheduler is running again (it was down since {fmtDateTime(recovered.at(-1)!.since)}, cleared {fmtDateTime(recovered.at(-1)!.cleared_at)}).
        </p>
      )}
      {api.setSchedulerDown && (
        <p className="muted">
          Demo only (mock adapter):{" "}
          <button
            type="button"
            className="btn btn-small"
            data-testid="toggle-scheduler"
            onClick={() => api.setSchedulerDown!(down.length === 0).then(loadAlerts)}
          >
            {down.length === 0 ? "Simulate scheduler down" : "Simulate scheduler back up"}
          </button>
        </p>
      )}
      {seesEffort && <EffortCard metrics={effort} />}
      {error && <p className="banner banner-bad" role="alert">Could not load metrics: {error}</p>}
      {!m && !error && <p role="status">Loading metrics...</p>}
      {m && (
        <>
          <div className="kpis">
            <div className="kpi" data-testid="kpi-p50"><span className="kpi-label">Median time to closure (P50)</span><span className="kpi-value"><Days value={m.p50_days} /></span><span className="kpi-note">Calendar time from result to first safe close. Synthetic cases.</span></div>
            <div className="kpi" data-testid="kpi-p90"><span className="kpi-label">P90 time to closure</span><span className="kpi-value"><Days value={m.p90_days} /></span><span className="kpi-note">9 in 10 closed cases close within this time. Synthetic cases.</span></div>
          </div>
          <div className="charts">
            <BarChart
              title="Open cases by age"
              unit="cases"
              data={BUCKETS.map((b) => ({ label: `${b} days`, value: m.open_by_age_bucket[b] ?? 0 }))}
              colorVar="--c-1"
            />
            <BarChart
              title="SLA breaches by provider"
              unit="breaches"
              data={m.breaches_by_provider.map((p) => ({ label: p.provider, value: p.count }))}
              colorVar="--c-2"
              horizontal
            />
            <BarChart
              title="Bottleneck: median days per transition"
              unit="days"
              data={m.bottleneck_by_transition.map((t) => ({ label: titleCase(t.transition), value: t.median_days }))}
              colorVar="--c-3"
              horizontal
            />
          </div>
        </>
      )}

      <section className="panel" aria-labelledby="nobody-h" data-testid="nobody-panel">
        <h2 id="nobody-h">Cases nobody is looking at</h2>
        <p className="muted">Open cases the watchdog flagged because nothing has happened on them for too long. Someone needs to take each one.</p>
        {flaggedError && <p className="banner banner-bad" role="alert">Could not load flagged cases: {flaggedError}</p>}
        {!flagged && !flaggedError && <p role="status">Loading flagged cases...</p>}
        {flagged && flagged.length === 0 && <p className="banner banner-ok" data-testid="nobody-empty">No flagged cases. Every open case has had recent activity.</p>}
        {flagged && flagged.length > 0 && (
          <ul className="plain nobody" data-testid="nobody-list">
            {flagged.map((c) => (
              <li key={c.case_id} data-testid="nobody-row">
                <Link to={`/coordinator/${c.case_id}`}>{shortId(c.case_id)}</Link> <StatePill state={c.state} />{" "}
                <WatchdogMarker flag={c.watchdog_flagged!} compact />
                <span className="muted"> {watchdogDetail(c.watchdog_flagged!)} Owner: {c.owner ?? "nobody"}. Last activity {fmtDateTime(c.last_event_at)}.</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
