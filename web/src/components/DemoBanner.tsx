import { useApp } from "../app/AppContext";
import { STAFF } from "../i18n/strings";

/**
 * The hosted judge demo's strip (item 8): "Demo, synthetic data, simulated time", on every page including the sign-in page and the
 * patient app. Shown only while `GET /health` reports the declared demo clock (`demo.simulated_clock`: DEMO_SIMULATED_CLOCK=1 and
 * DEMO_DEPLOYMENT=1 together, app.auth.demo_clock_active), so a production deployment never shows it.
 */
export function DemoBanner() {
  const { demoClock, simTime } = useApp();
  if (!demoClock) return null;
  return (
    <div className="demo-strip" role="status" data-testid="demo-clock-banner" title={STAFF.demoClockBanner.explanation}>
      <strong>{STAFF.demoClockBanner.label}</strong>
      {simTime ? <span> (simulated date {simTime.slice(0, 10)})</span> : null}
      <span className="demo-strip-note"> {STAFF.demoClockBanner.short}</span>
    </div>
  );
}
