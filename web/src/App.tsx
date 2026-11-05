import { Link, Navigate, Route, Routes } from "react-router-dom";
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
const PatientPage = lazy(() => import("./pages/PatientPage").then((m) => ({ default: m.PatientPage })));
const PatientHome = lazy(() => import("./pages/PatientPage").then((m) => ({ default: m.PatientHome })));
import { DEMO_PATIENT_CASE_ID } from "./api/fixtures";
import { Suspense, lazy, useEffect, useSyncExternalStore } from "react";
import { useApp } from "./app/AppContext";
import { DEMO_USERS, JUDGE_ROLES } from "./api/identities";
import { DemoBanner } from "./components/DemoBanner";
import { BrandLockup } from "./components/Brand";

/** OIDC mode (A29): shown until someone has signed in at the identity provider. Demo users are listed to pre-fill the login form. */
export function SignInPage({ error }: { error?: string | null }) {
  const { api, demoDeployment } = useApp();
  const oidc = api.oidc!;
  if (demoDeployment) return <JudgeSignIn error={error} />;
  return (
    <main className="container" id="main">
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

/**
 * The hosted judge demo's sign-in page (item 8). One card per role; each starts the real OIDC sign-in (authorization code with PKCE)
 * with that role's username pre-filled. The password is not in the page, the bundle or the health answer: it is a deploy-time
 * secret in Secret Manager and goes into the submission, and the identity provider asks for it. Rendered only when the server
 * reports `demo.deployment`; a production deployment never reports it, so it lists no accounts at all.
 */
function JudgeSignIn({ error }: { error?: string | null }) {
  const { api } = useApp();
  const oidc = api.oidc!;
  return (
    <main className="container" id="main" data-testid="judge-signin">
      <DemoBanner />
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Screening Closure: judge demo</h1>
      <p className="lead">Pick a role. You sign in through the real identity provider; the username is filled in and the <strong>password for each role is in the submission</strong>. Everything here is synthetic.</p>
      {error && <p role="alert" className="why">{error}</p>}
      <ul className="role-cards" data-testid="role-cards">
        {JUDGE_ROLES.map((r) => (
          <li key={r.role} data-testid={`role-card-${r.role}`}>
            <strong>{r.title}</strong>
            <span>{r.does}</span>
            <span className="muted">{r.tryThis}</span>
            <span className="mono role-user">Username: {r.username}</span>
            <button className="btn btn-primary" onClick={() => void oidc.signIn({ loginHint: r.username, forceLogin: true })}>Sign in as {r.title.toLowerCase()}</button>
          </li>
        ))}
      </ul>
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
  if (api.oidc && !signedIn) return <SignInPage error={signInError} />;
  return (
    <TourProvider>
      <ScrollRegions />
      <a className="skip-link" href="#main">Skip to content</a>
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
