import { useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { CaseSummary } from "../api/contract";
import { PathwayBadge } from "../components/PathwayBadge";
import { fmtDate, shortId } from "../lib/format";
import { DEFAULT_FILTERS, worklist } from "../lib/worklist";
import { StatePill } from "./CoordinatorPage";

/** The states in which a physician's signature is the next step. */
export const SIGNATURE_STATES: readonly string[] = ["confirmatory_completed", "clinician_reviewed", "reopened", "declined_pending_signoff"];

/**
 * The physician's own cases (everything she owns, ordered like the worklist), loaded once. `mine` matches the case page's own test:
 * the owner's actor string when the wire gave one, otherwise the owner's name.
 */
export function useMyCases(): { mine: CaseSummary[]; awaiting: CaseSummary[]; loaded: boolean } {
  const { api, actor, actorName, simTime, role } = useApp();
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  useEffect(() => {
    let live = true; // keep the previous list on screen while a role change re-reads it: no flicker
    api.listCases().then((c) => live && setCases(c)).catch(() => live && setCases([]));
    return () => { live = false; };
  }, [api, role]);
  return useMemo(() => {
    const all = worklist(cases ?? [], DEFAULT_FILTERS, simTime);
    const mine = all.filter((c) => (c.owner_actor ? c.owner_actor === actor : c.owner === actorName));
    return { mine, awaiting: mine.filter((c) => SIGNATURE_STATES.includes(c.state)), loaded: cases !== null };
  }, [cases, simTime, actor, actorName]);
}

/**
 * The physician's first screen after signing in: straight onto the case that waits for her signature, or her own page when there is
 * none (or the case list could not be read).
 */
export function PhysicianLanding() {
  const { awaiting, loaded } = useMyCases();
  if (!loaded) return <main className="container"><p role="status">Finding the case that needs your signature...</p></main>;
  return <Navigate to={awaiting[0] ? `/coordinator/${awaiting[0].case_id}` : "/physician"} replace />;
}

function CaseRow({ c, cta }: { c: CaseSummary; cta?: string }) {
  return (
    <li className="physician-case" data-testid={`physician-case-${c.case_id}`}>
      <div>
        <strong className="mono">{shortId(c.case_id)}</strong> <StatePill state={c.state} /> <PathwayBadge pathway={c.pathway} />
        <div className="muted">Last activity {fmtDate(c.last_event_at)}{c.due_at ? ` · due ${fmtDate(c.due_at)}` : ""}</div>
      </div>
      <Link className={cta ? "btn btn-primary" : "btn"} to={`/coordinator/${c.case_id}`}>{cta ?? "Open"}</Link>
    </li>
  );
}

/** "My cases": what waits for this physician's signature first, then the rest of the cases she owns. */
export function PhysicianPage() {
  const { actorName } = useApp();
  const { mine, awaiting, loaded } = useMyCases();
  const rest = mine.filter((c) => !awaiting.includes(c));
  return (
    <main className="container" id="physician-main" data-testid="physician-page">
      <h1>Your cases</h1>
      <p className="lead">{actorName}, a physician who owns these cases. A case closes only with evidence and your signature; nothing automated can sign for you.</p>
      {!loaded && <p role="status">Loading your cases...</p>}
      {loaded && (
        <>
          <section aria-labelledby="needs-sig-h">
            <h2 id="needs-sig-h">Needs your signature ({awaiting.length})</h2>
            {awaiting.length === 0 ? (
              <p className="muted" data-testid="physician-none-awaiting">Nothing is waiting for your signature right now. Cases appear here once the colonoscopy report is recorded.</p>
            ) : (
              <ul className="plain physician-list">{awaiting.map((c) => <CaseRow key={c.case_id} c={c} cta="Review and sign" />)}</ul>
            )}
          </section>
          <section aria-labelledby="other-h">
            <h2 id="other-h">Your other open cases ({rest.filter((c) => c.state !== "closed").length})</h2>
            {rest.filter((c) => c.state !== "closed").length === 0 ? (
              <p className="muted">You do not own any other open case.</p>
            ) : (
              <ul className="plain physician-list">{rest.filter((c) => c.state !== "closed").slice(0, 15).map((c) => <CaseRow key={c.case_id} c={c} />)}</ul>
            )}
            <p><Link to="/coordinator">See the full worklist</Link></p>
          </section>
        </>
      )}
    </main>
  );
}
