import headline from "./headline.json";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { ApiClient, CaseSummary } from "../api/contract";
import { DEFAULT_FILTERS, worklist } from "../lib/worklist";
import { DOCS, docHref, type DocLink } from "./links";

/**
 * Judge tour (C13): a guided, step-by-step walk through the demo story on whatever data the app currently has.
 * It only navigates and reads (`listCases`); it never posts an event, so a tour cannot change a case. It makes no external
 * calls: the document links are ordinary anchors that open in a new tab when clicked. The panel is not modal, so the page
 * behind it stays visible and usable, and it is operable by keyboard (Next, Back and End tour are buttons, Escape ends it).
 */

interface TourControl { open: () => void; isOpen: boolean }
const TourContext = createContext<TourControl>({ open: () => undefined, isOpen: false });
export const useTour = () => useContext(TourContext);

interface Step {
  id: string;
  title: string;
  body: ReactNode;
  /** The route to show, resolved from the current data. `null` keeps the page where it is. */
  route: (cases: CaseSummary[], now: string | null) => string | null;
  /** CSS selector of the element to outline once the page has rendered. */
  target?: string;
  links?: DocLink[];
}

const firstCase = (cases: CaseSummary[], now: string | null) => worklist(cases, DEFAULT_FILTERS, now)[0];
const caseIn = (states: string[]) => (cases: CaseSummary[], now: string | null) =>
  worklist(cases, DEFAULT_FILTERS, now).find((c) => states.includes(c.state)) ?? firstCase(cases, now);
const caseRoute = (pick: (cases: CaseSummary[], now: string | null) => CaseSummary | undefined) => (cases: CaseSummary[], now: string | null) => {
  const c = pick(cases, now);
  return c ? `/coordinator/${c.case_id}` : "/coordinator";
};

export const TOUR_STEPS: Step[] = [
  {
    id: "problem",
    title: "The problem: a positive result can go quiet",
    body: (
      <>
        <p>A positive screening result is only the start. The patient still needs an owner, a booking and a colonoscopy, and nothing in today&apos;s process notices when a case stops moving.</p>
        <p>This worklist is every open case: <strong>age</strong>, <strong>owner</strong>, <strong>next due date</strong> and <strong>flags</strong>, most overdue first. Red means act now, amber means held or escalated. Nothing here diagnoses anyone.</p>
      </>
    ),
    route: () => "/coordinator",
    target: '[data-testid="worklist"]',
    links: [DOCS.evidence],
  },
  {
    id: "timeline",
    title: "One case, one story",
    body: (
      <>
        <p>Every action on a case is an event in an append-only log. The dot shows who acted: a solid dot is a person, a green ring is the system, an amber diamond is an AI draft that a person must approve. Squares are milestones; red marks one that was missed.</p>
        <p>The case shown is the most overdue one on the current data.</p>
      </>
    ),
    route: caseRoute(firstCase),
    target: '[data-testid="timeline"]',
    links: [DOCS.aiBoundary],
  },
  {
    id: "guard",
    title: "The closure guard refuses bad evidence",
    body: (
      <>
        <p>A case closes only when a named clinician signs <em>and</em> the evidence exists and is complete. The guard refuses, with a 422, a close that has: no evidence reference, no signer, an automated actor, a Procedure alone, a stopped procedure, or a report that says the colonoscopy was incomplete. The case simply stays open.</p>
        <p>This tour posts nothing. To see the refusal, switch to the owner role and try a made-up reference in the sign-off panel (outlined when the case is at that stage).</p>
      </>
    ),
    route: caseRoute(caseIn(["confirmatory_completed", "clinician_reviewed", "reopened"])),
    target: '#signoff-panel, [data-testid="timeline"]',
    links: [DOCS.clinician],
  },
  {
    id: "agency",
    title: "The agency inbox: only decisions reach a person",
    body: (
      <>
        <p>Behind the worklist, agents prepare the routine work for every open case: drafts, referral packs, closure packs. They act through the same API and approval gate as a person, and none can close a case, accept an escalation, approve a message or sign anything.</p>
        <p>What lands in the inbox is a human decision, with the prepared material attached.</p>
      </>
    ),
    route: () => "/agency",
    target: '[data-testid="agency-inbox-panel"], [data-testid="agency-principle"]',
    links: [DOCS.agency],
  },
  {
    id: "effort",
    title: "The effort counter: human versus automated",
    body: (
      <>
        <p>Human actions against automated actions, counted from the event log, with the reduction in human time against the programme target of 40%.</p>
        <p>The minutes behind the percentage are <strong>assumed, not measured</strong>. In the literature-anchored simulation the target is <strong>not met in realistic scenarios</strong>: {headline.labour_reduction.literature_all.value} across all cases, {headline.labour_reduction.literature_in_reach.value} among cases the programme reaches; only the upper bound comes close. The card shows the live figure for the data you are on.</p>
      </>
    ),
    route: () => "/dashboard",
    target: '[data-testid="effort-card"]',
    links: [DOCS.results],
  },
  {
    id: "numbers",
    title: "The numbers, and where they come from",
    body: (
      <>
        <p>Time to closure, open cases by age, breaches by provider and bottleneck by transition. Every value is synthetic. On the mock adapter these are fixed fixture values; against the API they are computed from the cases in the database.</p>
        <p>The simulation results, the metric definitions and the limitations are in the repository.</p>
      </>
    ),
    route: () => "/dashboard",
    target: '[data-testid="kpi-p50"]',
    links: [DOCS.results, DOCS.metrics, DOCS.limits, DOCS.provenance],
  },
  {
    id: "summary",
    title: "One page to take away",
    body: <p>The judge summary has the positioning line, the headline with its caveats, the links to the repository documents and the command that re-runs every check.</p>,
    route: () => "/judge",
    links: [DOCS.demo],
  },
];

/** Resolve the steps' routes with the real data; a failing or empty read falls back to the worklist. */
async function loadCases(api: ApiClient): Promise<CaseSummary[]> {
  try {
    return await api.listCases();
  } catch {
    return [];
  }
}

export function TourProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const open = useCallback(() => setIsOpen(true), []);
  const value = useMemo(() => ({ open, isOpen }), [open, isOpen]);
  return (
    <TourContext.Provider value={value}>
      {children}
      {isOpen && <TourPanel onClose={() => setIsOpen(false)} />}
    </TourContext.Provider>
  );
}

function TourPanel({ onClose }: { onClose: () => void }) {
  const { api, role, setRole, simTime } = useApp();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [index, setIndex] = useState(0);
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const step = TOUR_STEPS[index];
  const last = index === TOUR_STEPS.length - 1;

  // The tour acts as the coordinator: the agency page and the effort card are for coordinator and supervisor only.
  useEffect(() => {
    if (role !== "coordinator" && role !== "supervisor") setRole("coordinator");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let live = true;
    void Promise.resolve().then(() => loadCases(api)).then((c) => live && setCases(c));
    return () => { live = false; };
  }, [api, role]);

  // Go to the step's page once the data it depends on has been read.
  useEffect(() => {
    if (cases === null) return;
    const to = step.route(cases, simTime);
    if (to && to !== pathname) navigate(to);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, cases]);

  useEffect(() => { headingRef.current?.focus(); }, [index]);

  // Outline the step's target once it renders (it may load after the route changes).
  useEffect(() => {
    if (!step.target) return;
    let el: Element | null = null;
    let tries = 0;
    const find = () => {
      el = document.querySelector(step.target!);
      if (el) {
        el.classList.add("tour-highlight");
        const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        el.scrollIntoView?.({ block: "start", behavior: reduce ? "auto" : "smooth" });
        return true;
      }
      return false;
    };
    const timer = window.setInterval(() => { if (find() || ++tries > 20) window.clearInterval(timer); }, 250);
    return () => {
      window.clearInterval(timer);
      document.querySelectorAll(".tour-highlight").forEach((n) => n.classList.remove("tour-highlight"));
    };
  }, [index, pathname, step]);

  useEffect(() => {
    document.body.classList.add("tour-open");
    return () => document.body.classList.remove("tour-open");
  }, []);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") onClose();
  };

  return (
    <section className="tour" role="dialog" aria-modal="false" aria-labelledby="tour-title" aria-describedby="tour-body" data-testid="judge-tour" onKeyDown={onKey}>
      <div className="tour-top">
        <span className="chip chip-synthetic">Judge tour</span>
        <span className="muted" data-testid="tour-progress" aria-live="polite">Step {index + 1} of {TOUR_STEPS.length}</span>
        <button type="button" className="btn btn-small" onClick={onClose} data-testid="tour-close">End tour</button>
      </div>
      <h2 id="tour-title" tabIndex={-1} ref={headingRef}>{step.title}</h2>
      <div id="tour-body" className="tour-body">{step.body}</div>
      {step.links && (
        <ul className="plain tour-links" aria-label="Documents for this step">
          {step.links.map((l) => (
            <li key={l.path}>
              <a href={docHref(l)} target="_blank" rel="noopener noreferrer">{l.label}</a> <span className="mono muted">{l.path}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="tour-nav">
        <button type="button" className="btn" onClick={() => setIndex((i) => Math.max(0, i - 1))} disabled={index === 0} data-testid="tour-back">Back</button>
        {last ? (
          <button type="button" className="btn btn-primary" onClick={onClose} data-testid="tour-finish">Finish</button>
        ) : (
          <button type="button" className="btn btn-primary" onClick={() => setIndex((i) => i + 1)} data-testid="tour-next">Next</button>
        )}
      </div>
    </section>
  );
}
