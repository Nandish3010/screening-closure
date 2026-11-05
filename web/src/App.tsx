import { Link, Navigate, Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { CoordinatorPage } from "./pages/CoordinatorPage";
import { TodayPage } from "./pages/TodayPage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { AgencyPage } from "./pages/AgencyPage";
import { JudgePage } from "./pages/JudgePage";
import { SafetyPage } from "./pages/SafetyPage";
import { IntegrationsPage } from "./pages/IntegrationsPage";
import { PilotValuePage } from "./pages/PilotValuePage";
import { TourProvider } from "./judge/JudgeTour";
import { DashboardPage } from "./pages/DashboardPage";
import { StartHerePage } from "./pages/StartHerePage";
import { PatientPage, PatientHome } from "./pages/PatientPage";
import { DEMO_PATIENT_CASE_ID } from "./api/fixtures";
import { useEffect, useState, useSyncExternalStore } from "react";
import { useApp } from "./app/AppContext";
import { DEMO_USERS, JUDGE_ROLES } from "./api/identities";
import type { DemoCredential } from "./api/contract";
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
  // ADR-057, opt-in: when the operator enabled it at deploy time the server lists the four logins WITH their generated passwords
  // (GET /demo/credentials), so a judge can copy one and sign in without leaving this page. Otherwise (the default) the password is
  // in the submission. None of it is in the web bundle or this repository.
  const [logins, setLogins] = useState<DemoCredential[] | null>(null);
  useEffect(() => {
    let live = true;
    void api.demoCredentials?.().then((l) => live && setLogins(l)).catch(() => undefined);
    return () => { live = false; };
  }, [api]);
  return (
    <main className="container" id="main" data-testid="judge-signin">
      <DemoBanner />
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Screening Closure: judge demo</h1>
      <p className="lead">Pick a role. You sign in through the real identity provider; the username is filled in and {logins ? <>the passwords are shown on the cards (copy one, then paste it at the sign-in)</> : <>the <strong>password for each role is in the submission</strong></>}. Everything here is synthetic.</p>
      {error && <p role="alert" className="why">{error}</p>}
      <ul className="role-cards" data-testid="role-cards">
        {JUDGE_ROLES.map((r) => (
          <li key={r.role} data-testid={`role-card-${r.role}`}>
            <strong>{r.title}</strong>
            <span>{r.does}</span>
            <span className="muted">{r.tryThis}</span>
            <span className="mono role-user">Username: {r.username}</span>
            {logins?.find((l) => l.role === r.role) && (
              <PasswordRow password={logins.find((l) => l.role === r.role)!.password} label={r.title} />
            )}
            <button className="btn btn-primary" onClick={() => void oidc.signIn({ loginHint: r.username, forceLogin: true })}>Sign in as {r.title.toLowerCase()}</button>
          </li>
        ))}
      </ul>
      <p className="muted">Once in, use <strong>Judge tour</strong> in the header for the five steps of the programme (record, prioritise, refer, reminders and navigation, outcome). You can switch role at any time with &quot;Sign in as&quot; in the header.</p>
    </main>
  );
}

/** One judge password, hidden until asked for, with a copy button. Comes from the server's deploy-time config, never from this bundle. */
const MASK = "••••••••••••";
function PasswordRow({ password, label }: { password: string; label: string }) {
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(password).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 2000); }, () => setShown(true));
  };
  return (
    <span className="role-pw" data-testid="role-password">
      <span className="mono">Password: {shown ? password : MASK}</span>{" "}
      <button type="button" className="btn btn-small" onClick={() => setShown((v) => !v)} aria-label={`${shown ? "Hide" : "Show"} the ${label} password`}>{shown ? "Hide" : "Show"}</button>{" "}
      <button type="button" className="btn btn-small" onClick={copy} aria-label={`Copy the ${label} password`}>{copied ? "Copied" : "Copy"}</button>
    </span>
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
  // ADR-057: on the hosted demo the first screen after sign-in is "Start here" (staff roles; the patient's first screen is their app).
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
      </div>
    </TourProvider>
  );
}
