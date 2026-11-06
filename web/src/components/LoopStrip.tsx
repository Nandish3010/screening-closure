import type { CaseEvent } from "../api/contract";
import { FUNNEL_STEPS } from "../lib/funnel";

/**
 * The five-step loop on one case: result recorded, higher-risk prioritised, referred, attended, outcome documented.
 * The same five steps and words the Outcomes dashboard counts, so a judge sees where THIS case sits in the programme loop.
 * Derived from the case's own events (the funnel's rules, applied to one case); nothing here is stored.
 */
export function loopProgress(events: CaseEvent[], opts: { prioritised: boolean; closed: boolean; declined?: boolean }): boolean[] {
  const has = (...types: string[]) => events.some((e) => types.includes(e.type));
  return [
    true,
    opts.prioritised,
    has("handoff_requested", "book_confirmatory"),
    has("complete_confirmatory"),
    opts.closed && !opts.declined,
  ];
}

export function LoopStrip({ events, prioritised, closed, declined }: { events: CaseEvent[]; prioritised: boolean; closed: boolean; declined?: boolean }) {
  const done = loopProgress(events, { prioritised, closed, declined });
  const current = done.findIndex((d, i) => !d && i > 0 && !(i === 1 && !prioritised));
  const doneCount = done.filter(Boolean).length;
  return (
    <nav className="loop-strip" aria-label={`The five-step loop: ${doneCount} of 5 done on this case`} data-testid="loop-strip">
      <ol>
        {FUNNEL_STEPS.map((s, i) => {
          const status = done[i] ? "done" : i === current ? "current" : i === 1 && !prioritised ? "na" : "todo";
          return (
            <li key={s.key} data-step={s.key} data-status={status} aria-current={status === "current" ? "step" : undefined}>
              <span className="loop-mark" aria-hidden="true">{status === "done" ? "✓" : i + 1}</span>
              <span className="loop-label">{s.label}</span>
              <span className="visually-hidden">{status === "done" ? ": done" : status === "current" ? ": this case is here" : status === "na" ? ": this pathway has no priority rules" : ": not yet"}</span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
