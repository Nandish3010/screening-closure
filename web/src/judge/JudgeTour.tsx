import headline from "./headline.json";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { ApiClient, CaseSummary, Role } from "../api/contract";
import { JUDGE_ROLES } from "../api/identities";
import { DEFAULT_FILTERS, worklist } from "../lib/worklist";
import { DOCS, docHref, type DocLink } from "./links";

/**
 * Judge tour (C13): a guided, step-by-step walk through the demo story on whatever data the app currently has.
 * It only navigates and reads (`listCases`); it never posts an event, so a tour cannot change a case. It makes no external
 * calls: the document links are ordinary anchors that open in a new tab when clicked. The panel is not modal, so the page
 * behind it stays visible and usable, and it is operable by keyboard (Next, Back and End tour are buttons, Escape ends it).
 */

/**
 * Two tracks. "challenge" (item 8) walks the challenge owner's five steps (record, prioritise, refer, reminders and navigation,
 * outcome) on the real screens, with "try it" prompts; the hosted demo opens it first. "story" is the original seven-step walk
 * through the demo story. Both only navigate and read: nothing in a tour posts an event.
 */
export type TourTrack = "challenge" | "story";
interface TourControl { open: (track?: TourTrack) => void; isOpen: boolean }
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
  /** The login that shows this step best (challenge track). The panel offers to switch when the current one differs. */
  role?: Role;
  /** "Try it" prompts: things the judge can do on the real screen. The tour itself never does them. */
  tryIt?: string[];
  /** Extra in-app deep links for this step. */
  also?: { label: string; to: string }[];
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

/** The path of the Today screen (the coordinator's start-of-day view). */
export const TODAY_ROUTE = "/coordinator/today";
/** The patient app's privacy centre (the access log and the patient's rights). */
export const PRIVACY_ROUTE = "/patient?tab=privacy";

/**
 * The challenge owner's five steps, each deep-linked into the real screens. The wording stays with what the screens show;
 * every number is on the screen or in the repository, none is typed here.
 */
export const CHALLENGE_STEPS: Step[] = [
  {
    id: "record",
    title: "1. Record: every positive result gets an owner and a clock",
    role: "coordinator",
    body: (
      <>
        <p>A positive screening result enters as a case. The system acknowledges it, names an owner and starts the clock, so nothing waits for someone to remember. The Today screen shows what needs a person today; each case page shows its whole history.</p>
        <p>Nothing here diagnoses anyone. The record is an append-only log of who did what and when.</p>
      </>
    ),
    route: () => TODAY_ROUTE,
    target: '[data-testid="today-screen"], [data-testid="worklist"]',
    tryIt: ["Open the first case from the Today screen and read its timeline from the top: who acted, and what is due next.", "On the worklist, find the owner and next-due columns for every open case."],
    also: [{ label: "Open the worklist", to: "/coordinator" }],
    links: [DOCS.evidence],
  },
  {
    id: "prioritise",
    title: "2. Prioritise: the people most at risk come first",
    role: "coordinator",
    body: (
      <>
        <p>Each case carries a priority tier (urgent, soon, routine) set at intake from a published rule; only the tier and the rule are stored, never the values. The risk score only orders the worklist. It never closes, escalates or decides care, and its thresholds are clinician placeholders until a clinical lead signs them off.</p>
      </>
    ),
    route: () => "/coordinator",
    target: '[data-testid="worklist"]',
    tryIt: ["Look at the Priority column: urgent cases sort above routine ones.", "Open a case and read why it has that tier (the explanation panel names the rule)."],
    links: [DOCS.riskCard, DOCS.limits],
  },
  {
    id: "refer",
    title: "3. Refer: ask the right service, and keep the owner until it accepts",
    role: "clinician",
    body: (
      <>
        <p>The case owner books a colonoscopy or hands the case to another service. A hand-off is a request: ownership stays with the referrer until the other side accepts, and a refusal or silence is re-routed rather than lost.</p>
      </>
    ),
    route: caseRoute(caseIn(["patient_contacted", "patient_acknowledged", "owner_accepted", "referral_rejected"])),
    target: '[data-testid="timeline"]',
    tryIt: ["Sign in as the clinician and, on a case you own, request a hand-off to a service; the timeline shows the request and who is waited on.", "Look for the Hand-off pending chip on the worklist."],
    also: [{ label: "Open the worklist", to: "/coordinator" }],
    links: [DOCS.clinician],
  },
  {
    id: "remind",
    title: "4. Reminders and navigation: the patient is reached, and a quiet case is caught",
    role: "supervisor",
    body: (
      <>
        <p>The patient gets short reminders in Arabic or English and a page that says the one next step. If nobody acts, the clock escalates the case up a ladder to the supervisor and the programme coordinator, and a watchdog flags a case that has stopped moving.</p>
        <p>The demo runs on a simulated clock so you can see weeks pass.</p>
      </>
    ),
    route: () => "/coordinator",
    target: '[data-testid="worklist"]',
    tryIt: ["Sign in as the supervisor, type 15 in Advance clock (days) in the header and press Advance. Watch the safety net flag the case: overdue chips, then escalations.", "Then open the patient app to see the plain next step in the patient's language, and the privacy centre for who has looked at the record."],
    also: [{ label: "Patient app", to: "/patient" }, { label: "Privacy centre", to: PRIVACY_ROUTE }],
    links: [DOCS.agency],
  },
  {
    id: "outcome",
    title: "5. Outcome: a case closes only with evidence and a clinician's signature",
    role: "supervisor",
    body: (
      <>
        <p>The dashboard funnel counts the five steps for the whole programme (result recorded, prioritised, referred, attended, outcome documented) with the share kept at each step and the median days between them, plus the cases at risk of being lost. A case closes only when a named clinician signs and the evidence exists and is complete; the guard refuses anything else.</p>
        <p>All figures are synthetic and come from the cases in this demo.</p>
      </>
    ),
    route: () => "/dashboard",
    target: '[data-testid="funnel-card"], [data-testid="kpi-p50"]',
    tryIt: ["Change the priority filter on the funnel and watch the counts follow.", "As the clinician, open a case at the sign-off stage and try a made-up evidence reference: the guard refuses it and the case stays open."],
    also: [{ label: "Judge summary", to: "/judge" }],
    links: [DOCS.results, DOCS.limits],
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

const RESUME_KEY = "sc.tour.resume";
interface Resume { track: TourTrack; index: number }

/** A tour that was running when the judge switched login (the sign-in leaves the page): picked up again on return. */
function takeResume(): Resume | null {
  try {
    const raw = sessionStorage.getItem(RESUME_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(RESUME_KEY);
    const r = JSON.parse(raw) as Resume;
    return (r.track === "challenge" || r.track === "story") && Number.isInteger(r.index) ? r : null;
  } catch {
    return null;
  }
}

export function TourProvider({ children }: { children: ReactNode }) {
  const [resume] = useState<Resume | null>(takeResume);
  const [state, setState] = useState<{ track: TourTrack; start: number } | null>(resume ? { track: resume.track, start: resume.index } : null);
  const { demoDeployment } = useApp();
  const open = useCallback((track?: TourTrack) => setState({ track: track ?? (demoDeployment ? "challenge" : "story"), start: 0 }), [demoDeployment]);
  const isOpen = state !== null;
  const value = useMemo(() => ({ open, isOpen }), [open, isOpen]);
  return (
    <TourContext.Provider value={value}>
      {children}
      {state && <TourPanel key={state.track} track={state.track} start={state.start} onTrack={(t) => setState({ track: t, start: 0 })} onClose={() => setState(null)} />}
    </TourContext.Provider>
  );
}

function TourPanel({ onClose, track, start, onTrack }: { onClose: () => void; track: TourTrack; start: number; onTrack: (t: TourTrack) => void }) {
  const { api, role, setRole, simTime, demoDeployment } = useApp();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const steps = track === "challenge" ? CHALLENGE_STEPS : TOUR_STEPS;
  const [index, setIndex] = useState(Math.min(start, steps.length - 1));
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const step = steps[index];
  const last = index === steps.length - 1;

  // The story track acts as the coordinator: the agency page and the effort card are for coordinator and supervisor only.
  // (With a sign-in provider the login is the provider's, so nothing is switched here; the challenge track offers the switch per step.)
  useEffect(() => {
    if (track === "story" && !api.oidc && role !== "coordinator" && role !== "supervisor") setRole("coordinator");
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
    if (to && to !== pathname + search) navigate(to);
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

  // Switch to the step's role. A sign-in provider means leaving the page, so the tour's place is kept and picked up again on return.
  const switchTo = () => {
    if (!step.role) return;
    if (api.oidc) {
      const u = JUDGE_ROLES.find((r) => r.role === step.role);
      if (!u || !demoDeployment) return;
      try { sessionStorage.setItem(RESUME_KEY, JSON.stringify({ track, index } satisfies Resume)); } catch { /* the tour just closes */ }
      void api.oidc.signIn({ loginHint: u.username, forceLogin: true });
    } else {
      setRole(step.role);
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") onClose();
  };

  return (
    <section className="tour" role="dialog" aria-modal="false" aria-labelledby="tour-title" aria-describedby="tour-body" data-testid="judge-tour" onKeyDown={onKey}>
      <div className="tour-top">
        <span className="chip chip-synthetic">Judge tour</span>
        <span className="tour-track" role="group" aria-label="Tour">
          <button type="button" className="btn btn-small" aria-pressed={track === "challenge"} onClick={() => onTrack("challenge")} data-testid="track-challenge">Five steps</button>
          <button type="button" className="btn btn-small" aria-pressed={track === "story"} onClick={() => onTrack("story")} data-testid="track-story">Demo story</button>
        </span>
        <span className="muted" data-testid="tour-progress" aria-live="polite">Step {index + 1} of {steps.length}</span>
        <button type="button" className="btn btn-small" onClick={onClose} data-testid="tour-close">End tour</button>
      </div>
      <h2 id="tour-title" tabIndex={-1} ref={headingRef}>{step.title}</h2>
      <div id="tour-body" className="tour-body">{step.body}</div>
      {step.role && role !== step.role && (
        <p className="muted" data-testid="tour-role-hint">
          This step is clearest as the <strong>{step.role}</strong> (you are the {role}).{" "}
          <button type="button" className="btn btn-small" onClick={switchTo} data-testid="tour-switch-role">Sign in as {step.role}</button>
        </p>
      )}
      {step.tryIt && (
        <div className="tour-try" data-testid="tour-try">
          <strong>Try it</strong>
          <ul>{step.tryIt.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
      )}
      {step.also && (
        <div className="tour-actions" aria-label="Also open">
          {step.also.map((a) => <Link key={a.to} className="btn btn-small" to={a.to}>{a.label}</Link>)}
        </div>
      )}
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
