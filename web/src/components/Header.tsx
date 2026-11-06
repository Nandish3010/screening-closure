import { useState } from "react";
import { Link, NavLink, matchPath, useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../app/AppContext";
import { ROLES, type Role } from "../api/contract";
import { DEMO_PATIENT_CASE_ID } from "../api/fixtures";
import { DEMO_USERS, JUDGE_ROLES } from "../api/identities";
import { useTour } from "../judge/JudgeTour";
import { fmtDateTime } from "../lib/format";
import { DemoBanner } from "./DemoBanner";
import { ResetDemoControl } from "./DemoStatus";
import { BrandLockup } from "./Brand";

export function SimBadge() {
  const { simTime } = useApp();
  return (
    <div className="sim-badge" role="status" aria-label="Simulated time" data-testid="sim-badge">
      <span className="sim-label">SIMULATED TIME:</span> <time dateTime={simTime ?? undefined}>{simTime ? fmtDateTime(simTime) : "unavailable"}</time>
    </div>
  );
}

const DEMO_MAX_ADVANCE_DAYS = 7;

/**
 * Demo-only control for the supervisor role (the only role the API lets move the simulation clock):
 * POST /clock/advance. Not rendered by the mock adapter, whose clock is fixed.
 */
function ClockControl() {
  const { api, refreshSim, demoDeployment } = useApp();
  const [days, setDays] = useState(1);
  const maxDays = demoDeployment ? DEMO_MAX_ADVANCE_DAYS : 365; // the hosted demo's API refuses more than 7 days per click (ADR-060 addendum)
  const [status, setStatus] = useState<string | null>(null);
  if (!api.advanceClock) return null;
  const advance = () =>
    api.advanceClock!({ days })
      .then(() => { setStatus(null); refreshSim(); })
      .catch((e) => setStatus(String((e as Error).message)));
  return (
    <span className="clock-control" data-testid="clock-control">
      <label>
        <span>Advance clock (days)</span>{" "}
        <input type="number" min={1} max={maxDays} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Days to advance the simulation clock" />
      </label>{" "}
      <button className="btn btn-small" onClick={advance} data-testid="advance-clock" disabled={!(days > 0 && days <= maxDays)}>Advance</button>
      {status && <span role="alert" className="why">{status}</span>}
    </span>
  );
}

export function Header() {
  const { role, setRole, api, actorName, demoDeployment } = useApp();
  const navigate = useNavigate();
  const tour = useTour();
  const { pathname } = useLocation();

  /** "Sign in as": one click on the hosted demo (ADR-060), a fresh start as that person; otherwise (or if that fails) the identity provider. */
  const switchTo = async (username: string) => {
    const judge = demoDeployment && api.demoSignIn ? JUDGE_ROLES.find((r) => r.username === username) : undefined;
    if (judge) {
      try {
        await api.demoSignIn!(judge.role);
        window.location.assign("/"); // a clean start as the new person: /me, the tour and every page re-read
        return;
      } catch {
        /* fall through to the password sign-in */
      }
    }
    void api.oidc!.signIn({ loginHint: username, forceLogin: true });
  };

  const onRole = (r: Role) => {
    setRole(r);
    if (r === "patient") {
      // The patient sees the case the staff member was looking at (their patient_ref is read from it, E8).
      const here = matchPath("/coordinator/:caseId", pathname)?.params.caseId ?? matchPath("/patient/:caseId", pathname)?.params.caseId;
      navigate(here ? `/patient/${here}` : api.adapter === "http" ? "/patient" : `/patient/${DEMO_PATIENT_CASE_ID}`);
    } else if (pathname.startsWith("/patient")) {
      const here = matchPath("/patient/:caseId", pathname)?.params.caseId;
      navigate(here && api.adapter === "http" ? `/coordinator/${here}` : "/coordinator");
    }
  };

  return (
    <header className={`app-header${pathname.startsWith("/patient") ? " app-header-patient" : ""}`}>
      <DemoBanner />
      <div className="header-row">
        <Link className="brand" to="/">
          <BrandLockup /><span className="visually-hidden"> home</span>
        </Link>
        <div className="header-tools">
          <button type="button" className="btn btn-small btn-tour" onClick={() => tour.open()} aria-haspopup="dialog" data-testid="judge-tour-button">Judge tour</button>
          <SimBadge />
        </div>
      </div>
      <div className="header-row">
        <nav aria-label="Main">
          {demoDeployment && role !== "patient" && <NavLink to="/start">Start here</NavLink>}
          {(role === "coordinator" || role === "supervisor") && <NavLink to="/coordinator/today">Today</NavLink>}
          <NavLink to="/coordinator" className={({ isActive }) => (isActive && pathname !== "/coordinator/today" ? "active" : undefined)}>Coordinator</NavLink>
          <NavLink to="/dashboard">Dashboard</NavLink>
          {(role === "coordinator" || role === "supervisor") && <NavLink to="/agency">Agency</NavLink>}
          <NavLink to={`/patient/${DEMO_PATIENT_CASE_ID}`}>Patient view</NavLink>
          <NavLink to="/judge">Judge</NavLink>
          <NavLink to="/safety">AI and safety</NavLink>
          <NavLink to="/integrations">Integrations</NavLink>
          <NavLink to="/pilot-value">Pilot value</NavLink>
        </nav>
        <div className="header-tools">
          {api.oidc ? (
            <>
              <label className="role-switch">
                <span>Sign in as</span>
                <select
                  value=""
                  aria-label="Sign in as"
                  onChange={(e) => e.target.value && void switchTo(e.target.value)}
                >
                  <option value="">{role}</option>
                  {(demoDeployment ? JUDGE_ROLES.map((r) => ({ username: r.username, name: r.title, role: r.role })) : DEMO_USERS).map((u) => (
                    <option key={u.username} value={u.username}>{u.name} ({u.role})</option>
                  ))}
                </select>
              </label>
              <button className="btn btn-small" onClick={() => api.oidc!.signOut()}>Sign out</button>
            </>
          ) : (
            <label className="role-switch">
              <span className="rs-long">Role, view as <span className="demo-only">(demo only)</span></span>
              <span className="rs-short" aria-hidden="true">View as <span className="demo-only">(demo)</span></span>
              <select value={role} onChange={(e) => onRole(e.target.value as Role)} aria-label="Switch role">
                {ROLES.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </label>
          )}
          <span className="who" title="Acting as">{actorName}</span>
          {role === "supervisor" && <ClockControl />}
          {role === "supervisor" && demoDeployment && <ResetDemoControl />}
          <span className={`adapter adapter-${api.adapter}`} title="API adapter">{api.adapter}</span>
          {api.automation !== undefined && (
            <span className="adapter" title="The mock plays the automation actor (system:automation); add ?automation=off to the URL to switch it off" data-testid="automation-mode">
              automation {api.automation ? "on" : "off"}
            </span>
          )}
          {api.resetDemo && !demoDeployment && (
            <button className="btn btn-small" onClick={() => api.resetDemo!().then(() => location.reload())}>
              Reset demo
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
