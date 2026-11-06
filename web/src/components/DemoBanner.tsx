import { useState } from "react";
import { useApp } from "../app/AppContext";
import { STAFF } from "../i18n/strings";
import { DemoStatusLine } from "./DemoStatus";

/**
 * The hosted judge demo's strip (item 8): "Demo, synthetic data, simulated time", on every page including the sign-in page and the
 * patient app. Shown only while `GET /health` reports the declared demo clock (`demo.simulated_clock`: DEMO_SIMULATED_CLOCK=1 and
 * DEMO_DEPLOYMENT=1 together, app.auth.demo_clock_active), so a production deployment never shows it. ADR-058 adds the status line
 * (last reset, by whom, simulated time, next scheduled reset) because several judges share this one demo.
 */
export function DemoBanner() {
  const { demoClock, simTime, demoStatus } = useApp();
  if (!demoClock) return null;
  return (
    <div className="demo-strip demo-strip-merged" role="status" data-testid="demo-clock-banner" title={STAFF.demoClockBanner.explanation}>
      <strong>{STAFF.demoClockBanner.label}</strong>
      {simTime && !demoStatus ? <span> (simulated date {simTime.slice(0, 10)})</span> : null}
      <span className="demo-strip-note"> {STAFF.demoClockBanner.short}</span>
      {demoStatus ? <DemoStatusLine /> : null}
    </div>
  );
}

/**
 * Sticky amber strip on every route (sign-in, staff screens, patient app): a prototype, synthetic data, not a government service.
 * On a phone (480px and below) it is one 44px line that also carries the demo notice ("synthetic data, simulated time") so the
 * header is not two banners tall; the full wording stays in the page (visually hidden, still read by screen readers) and the
 * "More" button shows it. Nothing is removed on any width.
 */
export function PrototypeStrip() {
  const { demoClock } = useApp();
  const [open, setOpen] = useState(false);
  return (
    <div className={`proto-strip${open ? " is-open" : ""}`} lang="en" dir="ltr" role="note" data-testid="proto-strip">
      <span className="ps-full" data-testid="proto-strip-full">
        <span>{STAFF.protoStrip.label} {STAFF.protoStrip.notOfficial}</span>{" "}
        <span className="ps-short">{STAFF.protoStrip.synthetic}</span>
        {demoClock ? <span className="ps-demo" data-testid="proto-strip-demo"> {STAFF.demoClockBanner.label}.</span> : null}
      </span>
      <span className="ps-compact" aria-hidden="true" data-testid="proto-strip-compact">{demoClock ? STAFF.protoStrip.compactDemo : STAFF.protoStrip.compact}</span>
      <button type="button" className="ps-more" aria-expanded={open} onClick={() => setOpen((o) => !o)} data-testid="proto-strip-more">
        {open ? "Less" : "More"}<span className="visually-hidden"> about this prototype notice</span>
      </button>
    </div>
  );
}
