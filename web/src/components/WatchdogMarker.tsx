import type { WatchdogFlag } from "../api/contract";

export const WATCHDOG_TEXT = "Nobody is looking at this";

/** Why the watchdog flagged the case, in plain words. */
export function watchdogDetail(f: Pick<WatchdogFlag, "reason" | "idle_days">): string {
  if (f.reason === "scheduler_down") return "The scheduler is down, so SLA clocks are not being checked.";
  return f.idle_days !== undefined ? `Nothing has happened on this case for ${f.idle_days} day${f.idle_days === 1 ? "" : "s"}.` : "Nothing has happened on this case for a while.";
}

/** Red marker for a `watchdog_flagged` case. Shown in the worklist, the case page and the timeline. */
export function WatchdogMarker({ flag, compact }: { flag: WatchdogFlag; compact?: boolean }) {
  return (
    <span className="chip chip-watchdog" data-testid="watchdog-marker" title={watchdogDetail(flag)}>
      <span aria-hidden="true">&#9888; </span>
      {WATCHDOG_TEXT}
      {!compact && <span className="visually-hidden">. {watchdogDetail(flag)}</span>}
    </span>
  );
}
