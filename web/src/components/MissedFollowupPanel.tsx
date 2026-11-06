import { useState } from "react";
import { useApp } from "../app/AppContext";
import { ApiError, type MissedFollowupResult } from "../api/contract";
import { ActionButton } from "./ActionButton";
import { fmtDateTime } from "../lib/format";

const EVENT_WORDS: Record<string, string> = {
  mark_overdue: "The scheduler marked the follow-up overdue",
  escalate: "The scheduler escalated up the ladder",
  watchdog_flagged: "The watchdog flagged the case as quiet",
};

/**
 * "Simulate a missed follow-up" (hosted demo, ADR-058). Moves the shared simulated clock just past this case's follow-up date and
 * the supervisor rung, then the engine's own scheduler and watchdog do what they always do. The page then shows the events they
 * wrote on the timeline and the pending escalation, which a person accepts. This panel writes nothing itself.
 */
export function MissedFollowupPanel({ caseId, closed, onDone }: { caseId: string; closed: boolean; onDone: () => Promise<void> | void }) {
  const { api, role, refreshSim } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MissedFollowupResult | null>(null);
  if (!api.missedFollowup) return null;
  const why = role !== "supervisor" ? "Supervisors only (it moves the shared demo clock). Sign in as the supervisor to run it." : closed ? "This case is closed, so there is no follow-up to miss." : null;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.missedFollowup!(caseId);
      setResult(r);
      await onDone();
      refreshSim();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That did not work. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel demo-panel" aria-labelledby="missed-h" data-testid="missed-followup">
      <h3 id="missed-h">Demo: simulate a missed follow-up</h3>
      <p>Skip the demo clock past this case&apos;s follow-up date, as if nobody had acted. The real scheduler and watchdog then do their job: the case goes overdue, a reminder and then a supervisor escalation are written, and a person has to accept the escalation.</p>
      <p className="muted">The clock is shared: it moves forward for everyone using the demo, and a reset puts it back.</p>
      <ActionButton primary testId="simulate-missed" busy={busy} disabledReason={why} onClick={() => void run()}>Simulate a missed follow-up</ActionButton>
      {error && <p role="alert" className="why" data-testid="missed-error">{error}</p>}
      {result && (
        <div role="status" data-testid="missed-result">
          <p><strong>Caught.</strong> {result.days_skipped > 0 ? <>The clock moved {result.days_skipped} day{result.days_skipped === 1 ? "" : "s"} ({fmtDateTime(result.clock_from)} to {fmtDateTime(result.clock_to)}).</> : "The clock did not need to move."}</p>
          <ul>
            {result.new_events.length === 0 && <li>Nothing new was needed: the case was already overdue and escalated.</li>}
            {result.new_events.map((e, i) => <li key={i}>{EVENT_WORDS[e.type] ?? e.type}{e.actor_kind === "system" ? " (by the system, not a person)" : ""}</li>)}
          </ul>
          <p>Now look at the timeline below and at the pending escalation, then accept it as the person it is addressed to: <strong>this is the human decision the system waits for.</strong></p>
        </div>
      )}
    </section>
  );
}
