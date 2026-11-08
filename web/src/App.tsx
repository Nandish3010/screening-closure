import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Header } from "./components/Header";
import { CoordinatorPage } from "./pages/CoordinatorPage";
import { TodayPage } from "./pages/TodayPage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
const AgencyPage = lazy(() => import("./pages/AgencyPage").then((m) => ({ default: m.AgencyPage })));
const JudgePage = lazy(() => import("./pages/JudgePage").then((m) => ({ default: m.JudgePage })));
const SafetyPage = lazy(() => import("./pages/SafetyPage").then((m) => ({ default: m.SafetyPage })));
const IntegrationsPage = lazy(() => import("./pages/IntegrationsPage").then((m) => ({ default: m.IntegrationsPage })));
const PilotValuePage = lazy(() => import("./pages/PilotValuePage").then((m) => ({ default: m.PilotValuePage })));
import { TourProvider } from "./judge/JudgeTour";
const DashboardPage = lazy(() => import("./pages/DashboardPage").then((m) => ({ default: m.DashboardPage })));
import { StartHerePage } from "./pages/StartHerePage";
import { PreviewPage } from "./pages/PreviewPage";
import { PhysicianLanding, PhysicianPage } from "./pages/PhysicianPage";
const PatientPage = lazy(() => import("./pages/PatientPage").then((m) => ({ default: m.PatientPage })));
const PatientHome = lazy(() => import("./pages/PatientPage").then((m) => ({ default: m.PatientHome })));
import { DEMO_PATIENT_CASE_ID } from "./api/fixtures";
import { Suspense, lazy, useEffect, useSyncExternalStore } from "react";
import { SIGNIN_RETRY_MAX_MS, SignInNotice, signInIsWarming, signInRetryDelayMs, useDemoSignIn } from "./app/demoSignIn";
import { isOwnerScoped, useApp } from "./app/AppContext";
import { DEMO_USERS, JUDGE_ROLES } from "./api/identities";
import { DemoBanner, PrototypeStrip } from "./components/DemoBanner";
import { BrandLockup } from "./components/Brand";

/** OIDC mode (A29): shown until someone has signed in at the identity provider. Demo users are listed to pre-fill the login form. */
export function SignInPage({ error }: { error?: string | null }) {
  const { api, demoDeployment, healthKnown } = useApp();
  const oidc = api.oidc!;
  // Whether this is the hosted judge demo is only known once GET /health has answered: until then show a neutral loading state,
  // never the developer's "make oidc-up" page (it flashed up before the role cards).
  if (!healthKnown) return <SignInLoading />;
  if (demoDeployment) return <JudgeSignIn error={error} />;
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

export { SIGNIN_RETRY_MAX_MS, signInIsWarming, signInRetryDelayMs };

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
  const si = useDemoSignIn(api);
  const { phase, busyRole: busy, locked } = si;
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
      <SignInNotice s={si} />
      {error && !si.failed && <p role="alert" className="why">{error}</p>}
      <ul className="role-cards" data-testid="role-cards">
        {JUDGE_ROLES.map((r) => (
          <li key={r.role} data-testid={`role-card-${r.role}`}>
            <strong>{r.title}</strong>
            <span>{r.does}</span>
            <span className="muted">{r.tryThis}</span>
            {oneClick ? (
              <button className="btn btn-primary" disabled={locked} onClick={() => si.go(r.role)}>
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
        <p className="operator-signin">
          <button type="button" className="linklike operator-link" data-testid="password-signin" onClick={() => void oidc.signIn({ forceLogin: true })}>Operator sign-in</button>
          <span className="muted"> For the people who run this demo, through the identity provider. Judges do not need it.</span>
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
  const { role, demoDeployment, healthKnown, roleKnown } = useApp();
  // On the hosted demo the first screen after sign-in depends on who signed in: the patient's own app, the physician's own cases, and
  // "Start here" for the staff roles. Who signed in is only known once /me has answered, so wait for it rather than guess from a stale role.
  if (!healthKnown || !roleKnown) return <main className="container"><p role="status">Loading...</p></main>;
  if (role === "patient") return <Navigate to="/patient" replace />;
  if (isOwnerScoped(role)) return <PhysicianLanding />;
  if (demoDeployment) return <Navigate to="/start" replace />;
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
  const { api, role, roleKnown } = useApp();
  const signedIn = useSyncExternalStore(
    (cb) => api.oidc?.subscribe(cb) ?? (() => undefined),
    () => (api.oidc ? api.oidc.signedIn() : true),
  );
  const { pathname } = useLocation();
  // The public preview (screenshots only, no API call) is reachable without signing in, and signed in too.
  if (pathname === "/preview" || pathname.startsWith("/preview/")) return <PreviewPage />;
  if (api.oidc && !signedIn) return <SignInPage error={signInError} />;
  // A patient only ever sees the patient app: a staff link opened as the patient goes to /patient instead of a dead end.
  if (api.oidc && roleKnown && role === "patient" && !pathname.startsWith("/patient")) return <Navigate to="/patient" replace />;
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
          <Route path="/coordinator/today" element={isOwnerScoped(role) ? <Navigate to="/physician" replace /> : <TodayPage />} />
          <Route path="/physician" element={<PhysicianPage />} />
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
