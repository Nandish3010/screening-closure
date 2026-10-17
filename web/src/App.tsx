import { Link, Navigate, Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { CoordinatorPage } from "./pages/CoordinatorPage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { DashboardPage } from "./pages/DashboardPage";
import { PatientPage, PatientHome } from "./pages/PatientPage";
import { DEMO_PATIENT_CASE_ID } from "./api/fixtures";

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

export default function App() {
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
          <Route path="/patient" element={<PatientHome />} />
          <Route path="/patient/:caseId" element={<PatientPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </>
  );
}
