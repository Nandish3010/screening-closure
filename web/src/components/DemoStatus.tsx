import { useEffect, useRef, useState } from "react";
import { useApp } from "../app/AppContext";
import { ApiError } from "../api/contract";
import { fmtDateTime } from "../lib/format";

/** "Last reset 2026-11-08 06:00 UTC by the schedule": when and by whom the seeded state was last restored. */
export function lastResetText(s: { last_reset_at?: string | null; last_reset_by?: string | null } | null): string {
  if (!s?.last_reset_at) return "Last reset: not recorded yet (the demo is in its seeded state)";
  return `Last reset ${fmtDateTime(s.last_reset_at)} by ${s.last_reset_by || "an operator"}`;
}

/**
 * The hosted demo's status line (ADR-057): when the demo was last reset and by whom, the simulated time, and when the next scheduled
 * reset is due. Judges share one demo, so they need to know what state they are walking into. Rendered only in the demo.
 */
export function DemoStatusLine() {
  const { demoStatus, simTime } = useApp();
  if (!demoStatus) return null;
  return (
    <span className="demo-status" data-testid="demo-status">
      <strong>Demo status:</strong> {lastResetText(demoStatus)}
      {" · "}simulated time {simTime ? fmtDateTime(simTime) : "unavailable"}
      {demoStatus.next_auto_reset_at ? <> · next scheduled reset {fmtDateTime(demoStatus.next_auto_reset_at)}</> : null}
      {demoStatus.reset_pending ? <> · <strong>reset in progress</strong></> : null}
    </span>
  );
}

/**
 * "Reset the demo": supervisor only (the API enforces it and rate-limits it). It asks the host to restore the seeded snapshot; the
 * API restarts for a minute. Everyone sharing the demo loses their changes, so the button asks first and says so.
 */
export function ResetDemoControl() {
  const { api, role, demoStatus, refreshSim } = useApp();
  const [step, setStep] = useState<"idle" | "confirm" | "waiting">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const before = useRef<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearInterval(timer.current), []);
  if (!api.requestDemoReset || !demoStatus) return null;
  const allowed = role === "supervisor";

  const waitForRestart = () => {
    before.current = demoStatus.last_reset_at ?? null;
    let tries = 0;
    timer.current = window.setInterval(() => {
      tries += 1;
      api.health().then((h) => {
        if (h.demo && h.demo.reset_pending !== true && (h.demo.last_reset_at ?? null) !== before.current) {
          window.clearInterval(timer.current);
          window.location.reload();
        }
      }).catch(() => undefined); // the API is down while it restarts: keep waiting
      if (tries > 60) {
        window.clearInterval(timer.current);
        setStep("idle");
        setMessage("The reset is taking longer than expected. Reload the page in a minute; if the demo still looks used, ask the operator.");
      }
    }, 3000);
  };

  const go = async () => {
    setMessage(null);
    try {
      const r = await api.requestDemoReset!();
      setMessage(r.message);
      setStep("waiting");
      waitForRestart();
      refreshSim();
    } catch (e) {
      setStep("idle");
      setMessage(e instanceof ApiError ? e.message : "The reset could not be requested. Please try again in a moment.");
    }
  };

  return (
    <span className="reset-demo" data-testid="reset-demo">
      {step === "idle" && (
        <>
          <button type="button" className="btn btn-small" onClick={() => setStep("confirm")} disabled={!allowed} aria-describedby="reset-why" data-testid="reset-demo-open">Reset the demo</button>
          {!allowed && <span id="reset-why" className="muted"> Supervisors only: sign in as the supervisor.</span>}
        </>
      )}
      {step === "confirm" && (
        <span role="alertdialog" aria-label="Confirm reset" data-testid="reset-demo-confirm">
          Everyone using the demo loses their changes and it restarts for about a minute.{" "}
          <button type="button" className="btn btn-small btn-primary" onClick={() => void go()} data-testid="reset-demo-yes">Reset now</button>{" "}
          <button type="button" className="btn btn-small" onClick={() => setStep("idle")}>Cancel</button>
        </span>
      )}
      {step === "waiting" && <span role="status" data-testid="reset-demo-waiting">Resetting. This page reloads when the demo is back.</span>}
      {message && step !== "waiting" && <span role="alert" className="why" data-testid="reset-demo-message"> {message}</span>}
    </span>
  );
}
