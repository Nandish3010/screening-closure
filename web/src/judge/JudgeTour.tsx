import headline from "./headline.json";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { ApiClient, CaseSummary, GuideStepId, GuidedCase, Role } from "../api/contract";
import { JUDGE_ROLES } from "../api/identities";
import { DEFAULT_FILTERS, worklist } from "../lib/worklist";
import { DOCS, docHref, type DocLink } from "./links";
import { markSeen, useGuide } from "./guide";

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
interface TourControl { open: (track?: TourTrack, index?: number) => void; isOpen: boolean }
const TourContext = createContext<TourControl>({ open: () => undefined, isOpen: false });
export const useTour = () => useContext(TourContext);

interface Step {
  id: string;
  title: string;
  body: ReactNode;
  /** The route to show, resolved from the current data. `null` keeps the page where it is. */
  route: (cases: CaseSummary[], now: string | null, guided?: GuidedCase | null) => string | null;
  /** Challenge track: which guided step this is (its fresh case comes from `GET /demo/guide`). */
  guide?: GuideStepId;
  /** Challenge track: the one line this step proves, shown on the Start here card. */
  claim?: string;
  /** Challenge track: roughly how long the step takes. */
  seconds?: number;
  /** Challenge track: what to look for on the destination screen. */
  lookFor?: string[];
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

const guidedCase = (g: GuidedCase | null | undefined, fallback: string) => (g ? `/coordinator/${g.case_id}` : fallback);
/** Without a fresh guided case, each step still opens its own fitting case (not one shared page), so the steps stay distinct. */
const guidedOr = (g: GuidedCase | null | undefined, pick: (cases: CaseSummary[], now: string | null) => CaseSummary | undefined, cases: CaseSummary[], now: string | null, none = "/coordinator") => {
  if (g) return `/coordinator/${g.case_id}`;
  const c = pick(cases, now);
  return c ? `/coordinator/${c.case_id}` : none;
};
const nth = (states: string[], n: number) => (cases: CaseSummary[], now: string | null) => {
  const list = worklist(cases, DEFAULT_FILTERS, now);
  const fit = list.filter((c) => states.includes(c.state));
  return fit[n] ?? list[n] ?? list[0];
};

/**
 * The challenge owner's five steps, plus the safety net and privacy and consent, each deep-linked into the real screens. On the hosted
 * demo each step opens an UNTOUCHED case picked for this browser session (`GET /demo/guide`, ADR-058), so two judges do not collide;
 * elsewhere it falls back to the first fitting case. `claim` is the one line this step proves; `lookFor` says what to see on the
 * destination. The wording stays with what the screens show; every number is on the screen or in the repository, none is typed here.
 */
export const CHALLENGE_STEPS: Step[] = [
  {
    id: "record",
    guide: "record",
    title: "1. Record: every positive result gets an owner and a clock",
    claim: "A positive result becomes a case with a named owner and a deadline the moment it arrives.",
    seconds: 40,
    role: "coordinator",
    body: (
      <>
        <p>A positive screening result enters as a case. The system acknowledges it, names an owner and starts the clock, so nothing waits for someone to remember. The case page shows its whole history; the Today screen shows what needs a person today.</p>
        <p>Nothing here diagnoses anyone. The record is an append-only log of who did what and when.</p>
      </>
    ),
    route: (c, n, g) => guidedOr(g, nth(["acknowledged", "result_received", "owner_accepted"], 0), c, n, TODAY_ROUTE),
    target: '[data-testid="timeline"]',
    lookFor: ["The first events in the timeline were written by the system, not a person: the result was acknowledged and an owner was named.", "The SLA clock line near the top says when the next step is due."],
    tryIt: ["Read the timeline from the top: who acted, and what is due next."],
    also: [{ label: "Open Today", to: TODAY_ROUTE }, { label: "Open the worklist", to: "/coordinator" }],
    links: [DOCS.evidence],
  },
  {
    id: "prioritise",
    guide: "prioritise",
    title: "2. Prioritise: the people most at risk come first",
    claim: "Every case has a priority tier from a published rule, and the screen says why.",
    seconds: 40,
    role: "coordinator",
    body: (
      <>
        <p>Each case carries a priority tier (urgent, soon, routine) set at intake from a published rule; only the tier and the rule are stored, never the values. The tier only orders the worklist. It never closes, escalates or decides care, and its thresholds are clinician placeholders until a clinical lead signs them off.</p>
      </>
    ),
    route: (_c, _n, g) => guidedCase(g, "/coordinator"),
    target: '[data-testid="priority"], [data-testid="worklist"]',
    lookFor: ["The Priority line: the tier, and the rule that gave it in plain words.", "The note under it: priority orders the list but never changes the clock or closes a case.", "On the worklist, urgent cases sort above routine ones."],
    tryIt: ["Open the worklist and compare the Priority column with the order of the rows."],
    also: [{ label: "Open the worklist", to: "/coordinator" }],
    links: [DOCS.riskCard, DOCS.limits],
  },
  {
    id: "refer",
    guide: "refer",
    title: "3. Refer, considering insurance: the right service, and an owner until it accepts",
    claim: "A referral prefers a service in the patient's insurance network and says why; ownership stays until the other side accepts.",
    seconds: 50,
    role: "clinician",
    body: (
      <>
        <p>The case owner books a colonoscopy or hands the case to another service. The patient&apos;s insurance scheme decides which services are offered first. A hand-off is a request: ownership stays with the referrer until the other side accepts, and a refusal or silence is re-routed rather than lost.</p>
      </>
    ),
    route: (cases, now, g) => (g ? `/coordinator/${g.case_id}` : caseRoute(caseIn(["patient_contacted", "patient_acknowledged", "owner_accepted", "referral_rejected"]))(cases, now)),
    target: '[data-testid="insurance"], [data-testid="timeline"]',
    lookFor: ["The Insurance line: the scheme (for example Thiqa, Enhanced or Basic) and which providers are in its network.", "An out-of-network choice is flagged with a reason; it is never blocked.", "In the timeline, a hand-off is a request: the owner does not change until the other service accepts."],
    tryIt: ["Sign in as the clinician who owns the case, pick a provider in the hand-off panel and watch the network note change."],
    links: [DOCS.clinician],
  },
  {
    id: "remind",
    guide: "remind",
    title: "4. Remind and navigate: the patient is reached in their language",
    claim: "The patient gets a short reminder in Arabic or English and one clear next step; a person approves what goes out.",
    seconds: 50,
    role: "coordinator",
    body: (
      <>
        <p>Messages to the patient are short, in their language, and say one next step. A draft is approved by the case owner before it is sent, and the pre-approved reminders the system sends on its own are marked as automatic. The patient app shows the same next step in plain words.</p>
      </>
    ),
    route: (c, n, g) => guidedOr(g, nth(["patient_contacted", "owner_accepted"], 1), c, n),
    target: '[data-testid="drafts-panel"], [data-testid="worklist"]',
    lookFor: ["The Messages panel: drafts show who wrote them and wait for a person to approve.", "Reminders the system sent itself carry an automatic marker in the timeline."],
    tryIt: ["Open the patient app (link below) to see the next step as the patient reads it, then switch the language."],
    also: [{ label: "Patient app", to: "/patient" }],
    links: [DOCS.agency],
  },
  {
    id: "outcome",
    guide: "outcome",
    title: "5. Track the outcome: closed only with evidence and a clinician's signature",
    claim: "The dashboard counts the loop's five steps; a case closes only with complete evidence and a named clinician's signature.",
    seconds: 50,
    role: "supervisor",
    body: (
      <>
        <p>The dashboard funnel counts the five steps for the whole programme (result recorded, prioritised, referred, attended, outcome documented) with the share kept at each step and the median days between them, plus the cases at risk of being lost. A case closes only when a named clinician signs and the evidence exists and is complete; the guard refuses anything else.</p>
        <p>All figures are synthetic and come from the cases in this demo. A step with too few cases to compare says so instead of showing a percentage.</p>
      </>
    ),
    route: () => "/dashboard",
    target: '[data-testid="funnel-card"], [data-testid="kpi-p50"]',
    lookFor: ["Five funnel steps with counts, and the cases at risk of being lost.", "Where there are too few cases to compare, the share is replaced by a note instead of a percentage."],
    tryIt: ["Change the priority filter on the funnel and watch the counts follow.", "Then open the sign-off case (link below) as the clinician and try a made-up evidence reference: the guard refuses it and the case stays open."],
    also: [{ label: "Open a case at sign-off", to: "/coordinator?state=confirmatory_completed" }, { label: "Judge summary", to: "/judge" }],
    links: [DOCS.results, DOCS.limits],
  },
  {
    id: "safety",
    guide: "safety",
    title: "Safety net: a missed follow-up is caught, and a person must act",
    claim: "If a follow-up is missed the scheduler and watchdog catch it, escalate it, and wait for a person to accept the escalation.",
    seconds: 75,
    role: "supervisor",
    body: (
      <>
        <p>Silence is the dangerous failure. Press <strong>Simulate a missed follow-up</strong> on this case: the demo clock skips past its due date and the real scheduler and watchdog run. Nothing is faked: the engine writes the overdue marker and the escalations, and no machine can accept them.</p>
      </>
    ),
    route: (c, n, g) => guidedOr(g, nth(["owner_accepted", "patient_contacted"], 2), c, n),
    target: '[data-testid="missed-followup"], [data-testid="worklist"]',
    lookFor: ["After the button: an overdue chip, then a reminder and a supervisor escalation on the timeline, each written by the system.", "The escalation shows as waiting for a human to accept.", "Accept it as the supervisor (the panel further down): it clears, and the log keeps who accepted."],
    tryIt: ["Press the button, read the result, then accept the escalation."],
    links: [DOCS.agency],
  },
  {
    id: "privacy",
    guide: "privacy",
    title: "Privacy and consent: the patient decides, and can see who looked",
    claim: "The patient controls how they are contacted and can see everyone who opened their record.",
    seconds: 40,
    role: "coordinator",
    body: (
      <>
        <p>Every staff read of a case is recorded outside the clinical log, and the patient can read that list in their privacy centre. If the patient withdraws consent, contact stops at once and nobody can message them until it is restored.</p>
      </>
    ),
    route: (c, n, g) => guidedOr(g, nth(["patient_contacted", "owner_accepted"], 3), c, n),
    target: '[data-testid="access-audit"], [data-testid="consent-state"]',
    lookFor: ["The access log: who opened this case and when (one row per person per 15 minutes).", "The Consent line: the channels allowed, and a Frozen chip if consent was withdrawn."],
    tryIt: ["Open the patient's privacy centre (link below) to see the same list from the patient's side."],
    also: [{ label: "Patient privacy centre", to: PRIVACY_ROUTE }],
    links: [DOCS.provenance],
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
  const open = useCallback((track?: TourTrack, index?: number) => setState({ track: track ?? (demoDeployment ? "challenge" : "story"), start: index ?? 0 }), [demoDeployment]);
  const isOpen = state !== null;
  const value = useMemo(() => ({ open, isOpen }), [open, isOpen]);
  return (
    <TourContext.Provider value={value}>
      {children}
      {state && <TourPanel key={`${state.track}:${state.start}`} track={state.track} start={state.start} onTrack={(t) => setState({ track: t, start: 0 })} onClose={() => setState(null)} />}
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
  // The panel can be folded down to its title and Back/Next, so the screen behind it can be read and used on a phone.
  const [minimised, setMinimised] = useState(false);
  const { guide, loaded: guideLoaded } = useGuide(api, demoDeployment && track === "challenge");
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

  // Go to the step's page once the data it depends on has been read (the cases, and on the hosted demo this session's fresh case).
  useEffect(() => {
    if (cases === null || !guideLoaded) return;
    markSeen(step.id);
    const to = step.route(cases, simTime, step.guide ? guide?.[step.guide] : null);
    if (to && to !== pathname + search) navigate(to);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, cases, guideLoaded]);

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

  useEffect(() => { document.body.classList.toggle("tour-minimised", minimised); return () => document.body.classList.remove("tour-minimised"); }, [minimised]);

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
    <section className={`tour${minimised ? " tour-min" : ""}`} role="dialog" aria-modal="false" aria-labelledby="tour-title" aria-describedby="tour-body" data-testid="judge-tour" onKeyDown={onKey}>
      <div className="tour-top">
        <span className="chip chip-synthetic">Judge tour</span>
        <span className="tour-track" role="group" aria-label="Tour">
          <button type="button" className="btn btn-small" aria-pressed={track === "challenge"} onClick={() => onTrack("challenge")} data-testid="track-challenge">Five steps</button>
          <button type="button" className="btn btn-small" aria-pressed={track === "story"} onClick={() => onTrack("story")} data-testid="track-story">Demo story</button>
        </span>
        <span className="muted" data-testid="tour-progress" aria-live="polite">Step {index + 1} of {steps.length}</span>
        <button type="button" className="btn btn-small" onClick={() => setMinimised((m) => !m)} aria-expanded={!minimised} data-testid="tour-fold">{minimised ? "Show details" : "Minimise"}</button>
        <button type="button" className="btn btn-small" onClick={onClose} data-testid="tour-close">End tour</button>
      </div>
      <h2 id="tour-title" tabIndex={-1} ref={headingRef}>{step.title}</h2>
      <div className="tour-fold">
      <div id="tour-body" className="tour-body">{step.body}</div>
      {step.role && role !== step.role && (
        <p className="muted" data-testid="tour-role-hint">
          This step is clearest as the <strong>{step.role}</strong> (you are the {role}).{" "}
          <button type="button" className="btn btn-small" onClick={switchTo} data-testid="tour-switch-role">Sign in as {step.role}</button>
        </p>
      )}
      {step.claim && <p className="tour-claim" data-testid="tour-claim"><strong>{step.claim}</strong></p>}
      {demoDeployment && track === "challenge" && step.guide && guideLoaded && guide && !guide[step.guide] && (
        <p className="muted" data-testid="tour-no-fresh">No untouched case is left for this step, so this shows the first fitting case. A supervisor can use &quot;Reset the demo&quot; to start clean.</p>
      )}
      {step.lookFor && (
        <div className="tour-look" data-testid="tour-look">
          <strong>What to look for</strong>
          <ul>{step.lookFor.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
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
      </div>
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
