import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Header } from "./components/Header";
const CoordinatorPage = lazy(() => import("./pages/CoordinatorPage").then((m) => ({ default: m.CoordinatorPage })));
const TodayPage = lazy(() => import("./pages/TodayPage").then((m) => ({ default: m.TodayPage })));
const CaseDetailPage = lazy(() => import("./pages/CaseDetailPage").then((m) => ({ default: m.CaseDetailPage })));
const AgencyPage = lazy(() => import("./pages/AgencyPage").then((m) => ({ default: m.AgencyPage })));
const JudgePage = lazy(() => import("./pages/JudgePage").then((m) => ({ default: m.JudgePage })));
const SafetyPage = lazy(() => import("./pages/SafetyPage").then((m) => ({ default: m.SafetyPage })));
const IntegrationsPage = lazy(() => import("./pages/IntegrationsPage").then((m) => ({ default: m.IntegrationsPage })));
const PilotValuePage = lazy(() => import("./pages/PilotValuePage").then((m) => ({ default: m.PilotValuePage })));
import { TourProvider } from "./judge/JudgeTour";
const DashboardPage = lazy(() => import("./pages/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const StartHerePage = lazy(() => import("./pages/StartHerePage").then((m) => ({ default: m.StartHerePage })));
const PreviewPage = lazy(() => import("./pages/PreviewPage").then((m) => ({ default: m.PreviewPage })));
const PatientPage = lazy(() => import("./pages/PatientPage").then((m) => ({ default: m.PatientPage })));
const PatientHome = lazy(() => import("./pages/PatientPage").then((m) => ({ default: m.PatientHome })));
import { DEMO_PATIENT_CASE_ID } from "./api/demoIds";
import { Suspense, lazy, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ApiError, type Role } from "./api/contract";
import { useApp } from "./app/AppContext";
import { DEMO_USERS, JUDGE_ROLES } from "./api/identities";
import { DemoBanner, PrototypeStrip } from "./components/DemoBanner";
import { BrandLockup } from "./components/Brand";

const hostedBuild = () => String(import.meta.env?.VITE_HOSTED_DEMO ?? "") === "1";

/** OIDC mode (A29): shown until someone has signed in at the identity provider. Demo users are listed to pre-fill the login form. */
export function SignInPage({ error }: { error?: string | null }) {
  const { api, demoDeployment, healthKnown } = useApp();
  const oidc = api.oidc!;
  // Whether this is the hosted judge demo is only known once GET /health has answered: until then show a neutral loading state,
  // never the developer's "make oidc-up" page (it flashed up before the role cards).
  // The hosted judge build (VITE_HOSTED_DEMO=1, set only by the GCP edge image) is the demo by construction, so its role cards are shown
  // at once instead of after the /health round trip (about 1 s on a cold start); /health still decides for every other build.
  if (!healthKnown && !hostedBuild()) return <SignInLoading />;
  if (demoDeployment || (!healthKnown && hostedBuild())) return <JudgeSignIn error={error} />;
  return (
    <main className="container" id="main">
      <PrototypeStrip />
      <DemoBanner />
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Sign in</h1>
      <p className="lead">Identity comes from the identity provider (a local stand-in for UAE PASS in this build). The demo users' passwords are printed by <code>make oidc-up</code> and never shown here.</p>
      {error && <p role="alert" className="why">{error}</p>}
      <p><button className="btn" onClick={() => void oidc.signIn()}>Sign in</button></p>
      <h2>Sign in as</h2>
      <ul className="cards">
        {DEMO_USERS.map((u) => (
          <li key={u.username}>
            <button className="linklike" onClick={() => void oidc.signIn({ loginHint: u.username, forceLogin: true })}>
              <strong>{u.name}</strong><span>{u.role}</span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}

function SignInLoading() {
  return (
    <main className="container" id="main" data-testid="signin-loading">
      <p role="status" aria-live="polite">Loading...</p>
    </main>
  );
}

/** S1: how the one-click sign-in copes with a demo that is still starting (VM restart) and with a shared network that has used its allowance. */
export const SIGNIN_RETRY_MAX_MS = 120_000; // keep trying on its own for about two minutes, then hand the button back
const FINAL_SIGNIN_CODES = new Set(["signin_locked", "signin_refused", "signin_not_configured", "signin_unknown_role"]);
/** True when the identity provider (or the edge in front of it) is not ready yet: 502/503/504 or no answer at all, unless the API said it is final. */
export function signInIsWarming(e: unknown): boolean {
  return e instanceof ApiError && !(e.code && FINAL_SIGNIN_CODES.has(e.code)) && [0, 502, 503, 504].includes(e.status);
}
/** 3, 3, 4, 4, 5, 5, ... seconds (never less than the server's Retry-After, never more than 5 s unless it asks for more). */
export function signInRetryDelayMs(attempt: number, retryAfter?: number): number {
  return Math.max(Math.min(5, 3 + Math.floor(attempt / 2)), retryAfter && retryAfter <= 10 ? retryAfter : 0) * 1000;
}
type SignInPhase =
  | { kind: "idle" }
  | { kind: "signing"; role: Role }
  | { kind: "warming"; role: Role }
  | { kind: "gave-up"; role: Role }
  | { kind: "limited"; seconds: number };

/**
 * The hosted judge demo's sign-in page (item 8). One card per role; each starts the real OIDC sign-in (authorization code with PKCE)
 * with that role's username pre-filled. The password is not in the page, the bundle or the health answer: it is a deploy-time
 * secret in Secret Manager and goes into the submission, and the identity provider asks for it. Rendered only when the server
 * reports `demo.deployment`; a production deployment never reports it, so it lists no accounts at all.
 */
function JudgeSignIn({ error }: { error?: string | null }) {
  const { api } = useApp();
  const oidc = api.oidc!;
  const oneClick = typeof api.demoSignIn === "function";
  const [phase, setPhase] = useState<SignInPhase>({ kind: "idle" });
  const [failed, setFailed] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);
  const stop = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      stop();
    };
  }, [stop]);
  /** One click: try, and while the demo is starting up retry by itself (S1). `startedAt` is the first try of this click. */
  const go = useCallback(async (role: Role, attempt = 0, startedAt = Date.now()) => {
    stop();
    setFailed(null);
    setPhase(attempt === 0 ? { kind: "signing", role } : { kind: "warming", role });
    try {
      await api.demoSignIn!(role);
      if (alive.current) setPhase({ kind: "idle" }); // normally the page is replaced by the signed-in app before this matters
    } catch (e) {
      if (!alive.current) return;
      if (signInIsWarming(e)) {
        if (Date.now() - startedAt >= SIGNIN_RETRY_MAX_MS) {
          setPhase({ kind: "gave-up", role });
          return;
        }
        setPhase({ kind: "warming", role });
        timer.current = setTimeout(() => void go(role, attempt + 1, startedAt), signInRetryDelayMs(attempt, e instanceof ApiError ? e.retryAfter : undefined));
      } else if (e instanceof ApiError && e.status === 429) {
        setPhase({ kind: "limited", seconds: e.retryAfter ?? 60 });
      } else {
        setFailed(e instanceof Error && e.message ? e.message : "One-click sign-in did not work.");
        setPhase({ kind: "idle" });
      }
    }
  }, [api, stop]);
  // The "please wait N seconds" count-down for a 429; the buttons come back at zero.
  const waiting = phase.kind === "limited";
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => setPhase((p) => (p.kind !== "limited" ? p : p.seconds <= 1 ? { kind: "idle" } : { kind: "limited", seconds: p.seconds - 1 })), 1000);
    return () => clearInterval(id);
  }, [waiting]);
  const busy = phase.kind === "signing" ? phase.role : phase.kind === "warming" || phase.kind === "gave-up" ? phase.role : null;
  const locked = phase.kind !== "idle" && phase.kind !== "gave-up"; // buttons are off while a try, a retry or a wait is under way
  return (
    <main className="container" id="main" data-testid="judge-signin">
      <PrototypeStrip />
      <DemoBanner />
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Screening Closure: judge demo</h1>
      {oneClick ? (
        <p className="lead">Pick a role and you are in: <strong>no password needed</strong> on this demo. Everything here is synthetic.</p>
      ) : (
        <p className="lead">Pick a role. You sign in through the real identity provider; the username is filled in and the identity provider asks for the password (operators only; judges use the one-click sign-in where it is offered). Everything here is synthetic.</p>
      )}
      {phase.kind === "warming" && (
        <div role="status" className="why" data-testid="signin-warming">
          <p>The demo is starting up. Retrying automatically...</p>
          <button className="btn" onClick={() => void go(phase.role)}>Retry now</button>{" "}
          <button className="linklike" onClick={() => { stop(); setPhase({ kind: "idle" }); }}>Stop retrying</button>
        </div>
      )}
      {phase.kind === "gave-up" && (
        <div role="alert" className="why" data-testid="signin-gave-up">
          <p>The demo is taking longer than usual to start. Please try again in a minute, or sign in with a password.</p>
          <button className="btn" onClick={() => void go(phase.role)}>Retry</button>
        </div>
      )}
      {phase.kind === "limited" && (
        <p role="status" className="why" data-testid="signin-limited">Too many sign-ins from this network, please wait {phase.seconds} second{phase.seconds === 1 ? "" : "s"}, or sign in with a password.</p>
      )}
      {(error || failed) && <p role="alert" className="why">{failed ?? error}</p>}
      <ul className="role-cards" data-testid="role-cards">
        {JUDGE_ROLES.map((r) => (
          <li key={r.role} data-testid={`role-card-${r.role}`}>
            <strong>{r.title}</strong>
            <span>{r.does}</span>
            <span className="muted">{r.tryThis}</span>
            {oneClick ? (
              <button className="btn btn-primary" disabled={locked} onClick={() => void go(r.role)}>
                {busy === r.role && locked ? (phase.kind === "warming" ? "Starting up..." : "Signing in...") : `Log in as ${r.label}`}
              </button>
            ) : (
              <>
                <span className="mono role-user">Username: {r.username}</span>
                <button className="btn btn-primary" onClick={() => void oidc.signIn({ loginHint: r.username, forceLogin: true })}>Sign in as {r.label.toLowerCase()}</button>
              </>
            )}
          </li>
        ))}
      </ul>
      {oneClick && (
        <p className="muted">
          <button className="linklike" data-testid="password-signin" onClick={() => void oidc.signIn({ forceLogin: true })}>Operator sign-in (password)</button>
          {" "}(the real identity provider; for operators who hold a password).
        </p>
      )}
      <p className="preview-link"><Link className="btn" to="/preview" data-testid="preview-link">Preview without signing in</Link> <span className="muted">Screenshots of the five steps on synthetic data, and the headline numbers. No password needed.</span></p>
      <p className="muted">Once in, use <strong>Judge tour</strong> in the header for the five steps of the programme (record, prioritise, refer, reminders and navigation, outcome). You can switch role at any time with &quot;Sign in as&quot; in the header.</p>
    </main>
  );
}

function Landing() {
  return (
    <main className="container">
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Screening Closure</h1>
      <p className="lead">Every positive screening result has an owner, a clock and a signed closure. Nothing here diagnoses anyone.</p>
      <ul className="cards">
        <li><Link to="/coordinator/today"><strong>Coordinator: Today</strong><span>What needs a person now</span></Link></li>
        <li><Link to="/coordinator"><strong>Full worklist</strong><span>Every case, timeline, actions</span></Link></li>
        <li><Link to={`/patient/${DEMO_PATIENT_CASE_ID}`}><strong>Patient view</strong><span>Arabic and English, installable</span></Link></li>
        <li><Link to="/dashboard"><strong>Closure dashboard</strong><span>P50/P90, ageing, breaches</span></Link></li>
      </ul>
    </main>
  );
}

/** The coordinator's and the supervisor's landing page is Today (C14); everyone else sees the entry cards. */
function Home() {
  const { role, demoDeployment, healthKnown } = useApp();
  // ADR-058: on the hosted demo the first screen after sign-in is "Start here" (staff roles; the patient's first screen is their app).
  if (!healthKnown) return <main className="container"><p role="status">Loading...</p></main>;
  if (demoDeployment) return <Navigate to={role === "patient" ? "/patient" : "/start"} replace />;
  return role === "coordinator" || role === "supervisor" ? <Navigate to="/coordinator/today" replace /> : <Landing />;
}

/** Keyboard access to horizontally scrollable tables (axe: scrollable-region-focusable): every `.jp-tablewrap` becomes a focusable, labelled region. */
function ScrollRegions() {
  useEffect(() => {
    const fix = () => document.querySelectorAll<HTMLElement>(".jp-tablewrap:not([tabindex])").forEach((el) => {
      el.tabIndex = 0;
      el.setAttribute("role", "region");
      if (!el.hasAttribute("aria-label")) el.setAttribute("aria-label", "Table (scrolls sideways on a narrow screen)");
    });
    fix();
    const mo = new MutationObserver(fix);
    mo.observe(document.body, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, []);
  return null;
}

export default function App({ signInError = null }: { signInError?: string | null }) {
  const { api } = useApp();
  const signedIn = useSyncExternalStore(
    (cb) => api.oidc?.subscribe(cb) ?? (() => undefined),
    () => (api.oidc ? api.oidc.signedIn() : true),
  );
  const { pathname } = useLocation();
  // The public preview (screenshots only, no API call) is reachable without signing in, and signed in too.
  if (pathname === "/preview" || pathname.startsWith("/preview/")) return <Suspense fallback={<main className="container"><p role="status">Loading...</p></main>}><PreviewPage /></Suspense>;
  if (api.oidc && !signedIn) return <SignInPage error={signInError} />;
  return (
    <TourProvider>
      <ScrollRegions />
      <a className="skip-link" href="#main">Skip to content</a>
      <PrototypeStrip />
      <Header />
      <div id="main">
        <Suspense fallback={<main className="container"><p role="status">Loading this page...</p></main>}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/start" element={<StartHerePage />} />
          <Route path="/coordinator" element={<CoordinatorPage />} />
          <Route path="/coordinator/today" element={<TodayPage />} />
          <Route path="/coordinator/:caseId" element={<CaseDetailPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/judge" element={<JudgePage />} />
          <Route path="/safety" element={<SafetyPage />} />
          <Route path="/integrations" element={<IntegrationsPage />} />
          <Route path="/pilot-value" element={<PilotValuePage />} />
          <Route path="/agency" element={<AgencyPage />} />
          <Route path="/patient" element={<PatientHome />} />
          <Route path="/patient/:caseId" element={<PatientPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </div>
    </TourProvider>
  );
}
