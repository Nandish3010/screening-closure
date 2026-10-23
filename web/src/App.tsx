import { Link, Navigate, Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { CoordinatorPage } from "./pages/CoordinatorPage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { AgencyPage } from "./pages/AgencyPage";
import { DashboardPage } from "./pages/DashboardPage";
import { PatientPage, PatientHome } from "./pages/PatientPage";
import { DEMO_PATIENT_CASE_ID } from "./api/fixtures";
import { useSyncExternalStore } from "react";
import { useApp } from "./app/AppContext";
import { DEMO_USERS } from "./api/identities";

/** OIDC mode (A29): shown until someone has signed in at the identity provider. Demo users are listed to pre-fill the login form. */
export function SignInPage({ error }: { error?: string | null }) {
  const { api } = useApp();
  const oidc = api.oidc!;
  return (
    <main className="container" id="main">
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

function Landing() {
  return (
    <main className="container">
      <h1>Screening Closure</h1>
      <p className="lead">Every positive screening result has an owner, a clock and a signed closure. Nothing here diagnoses anyone.</p>
      <ul className="cards">
        <li><Link to="/coordinator"><strong>Coordinator app</strong><span>Worklist, timeline, actions</span></Link></li>
        <li><Link to={`/patient/${DEMO_PATIENT_CASE_ID}`}><strong>Patient view</strong><span>Arabic and English, installable</span></Link></li>
        <li><Link to="/dashboard"><strong>Closure dashboard</strong><span>P50/P90, ageing, breaches</span></Link></li>
      </ul>
    </main>
  );
}

export default function App({ signInError = null }: { signInError?: string | null }) {
  const { api } = useApp();
  const signedIn = useSyncExternalStore(
    (cb) => api.oidc?.subscribe(cb) ?? (() => undefined),
    () => (api.oidc ? api.oidc.signedIn() : true),
  );
  if (api.oidc && !signedIn) return <SignInPage error={signInError} />;
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <Header />
      <div id="main">
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/coordinator" element={<CoordinatorPage />} />
          <Route path="/coordinator/:caseId" element={<CaseDetailPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/agency" element={<AgencyPage />} />
          <Route path="/patient" element={<PatientHome />} />
          <Route path="/patient/:caseId" element={<PatientPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </>
  );
}
