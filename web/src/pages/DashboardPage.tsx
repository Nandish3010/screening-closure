import { useEffect, useState } from "react";
import { useApp } from "../app/AppContext";
import type { AgeBucket, ClosureMetrics } from "../api/contract";
import { BarChart } from "../components/BarChart";
import { titleCase } from "../lib/format";

const BUCKETS: AgeBucket[] = ["0-30", "31-60", "61-90", "90+"];

export function DashboardPage() {
  const { api } = useApp();
  const [m, setM] = useState<ClosureMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.closureMetrics().then((x) => live && setM(x)).catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [api]);

  return (
    <main className="container">
      <h1>Closure dashboard</h1>
      <p className="muted">Synthetic data on a simulated clock. Every number is regenerated from a fixed seed.</p>
      {error && <p className="banner banner-bad" role="alert">Could not load metrics: {error}</p>}
      {!m && !error && <p role="status">Loading metrics...</p>}
      {m && (
        <>
          <div className="kpis">
            <div className="kpi" data-testid="kpi-p50"><span className="kpi-label">Median time to closure (P50)</span><span className="kpi-value">{m.p50_days}<small> days</small></span></div>
            <div className="kpi" data-testid="kpi-p90"><span className="kpi-label">P90 time to closure</span><span className="kpi-value">{m.p90_days}<small> days</small></span></div>
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
    </main>
  );
}
