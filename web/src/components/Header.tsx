import { useState } from "react";
import { Link, NavLink, matchPath, useLocation, useNavigate } from "react-router-dom";
import { isOwnerScoped, useApp } from "../app/AppContext";
import { ROLES, type Role } from "../api/contract";
import { SignInNotice, useDemoSignIn } from "../app/demoSignIn";
import { DEMO_PATIENT_CASE_ID } from "../api/fixtures";
import { DEMO_USERS, JUDGE_ROLES, ROLE_DISPLAY } from "../api/identities";
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
  // S4: on the hosted demo the "Sign in as" switch is the same one-click sign-in as the role cards (same warming-up retry, same 429
  // handling) and never falls back to the password flow. A clean start as the new person: /me, the tour and every page re-read.
  const oneClick = !!(demoDeployment && api.oidc && api.demoSignIn);
  const si = useDemoSignIn(api, () => window.location.assign("/"));
  const patient = role === "patient";
  const staffTools = !patient;

  const switchTo = (username: string) => {
    const judge = oneClick ? JUDGE_ROLES.find((r) => r.username === username) : undefined;
    if (judge) return si.go(judge.role);
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

  const ownerRole = isOwnerScoped(role);
  return (
    <header className={`app-header${pathname.startsWith("/patient") ? " app-header-patient" : ""}${patient ? " app-header-role-patient" : ""}`}>
      <div className="header-row header-row-main">
        <Link className="brand" to="/">
          <BrandLockup /><span className="visually-hidden"> home</span>
        </Link>
        {staffTools && (
          <nav aria-label="Main">
            {ownerRole && <NavLink to="/physician">My cases</NavLink>}
            {demoDeployment && <NavLink to="/start">Start here</NavLink>}
            {(role === "coordinator" || role === "supervisor") && <NavLink to="/coordinator/today">Today</NavLink>}
            <NavLink to="/coordinator" className={({ isActive }) => (isActive && pathname !== "/coordinator/today" ? "active" : undefined)}>{ownerRole ? "All cases" : "Coordinator"}</NavLink>
            <NavLink to="/dashboard">Dashboard</NavLink>
            {(role === "coordinator" || role === "supervisor") && <NavLink to="/agency">Agency</NavLink>}
            <NavLink to={api.adapter === "http" ? "/patient" : `/patient/${DEMO_PATIENT_CASE_ID}`}>Patient view</NavLink>
            <NavLink to="/judge">Judge</NavLink>
            <NavLink to="/safety">AI and safety</NavLink>
            <NavLink to="/integrations">Integrations</NavLink>
            <NavLink to="/pilot-value">Pilot value</NavLink>
          </nav>
        )}
        {staffTools && (
          <div className="header-tools">
            <button type="button" className="btn btn-small btn-tour" onClick={() => tour.open()} aria-haspopup="dialog" data-testid="judge-tour-button">Judge tour</button>
          </div>
        )}
        {patient && <SimBadge />}
      </div>
      <div className="header-row header-row-sub">
        {staffTools && <SimBadge />}
        <DemoBanner />
        <div className="header-tools">
          {api.oidc ? (
            <>
              <label className="role-switch">
                <span>Sign in as</span>
                <select
                  value=""
                  aria-label="Sign in as"
                  disabled={si.locked}
                  onChange={(e) => e.target.value && switchTo(e.target.value)}
                >
                  <option value="">{ROLE_DISPLAY[role]}</option>
                  {(demoDeployment ? JUDGE_ROLES.map((r) => ({ username: r.username, name: r.title, role: r.role })) : DEMO_USERS.map((u) => ({ ...u, name: `${u.name} (${ROLE_DISPLAY[u.role]})` }))).map((u) => (
                    <option key={u.username} value={u.username}>{u.name}</option>
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
                  <option key={r} value={r}>{ROLE_DISPLAY[r]}</option>
                ))}
              </select>
            </label>
          )}
          {staffTools && <span className="who" title="Acting as">{actorName}</span>}
          {role === "supervisor" && <ClockControl />}
          {role === "supervisor" && demoDeployment && <ResetDemoControl />}
          {staffTools && !demoDeployment && <span className={`adapter adapter-${api.adapter}`} title="Where the data comes from">{api.adapter === "http" ? "live API" : "mock data"}</span>}
          {staffTools && !demoDeployment && api.automation !== undefined && (
            <span className="adapter" title="The mock plays the automation actor (system:automation); add ?automation=off to the URL to switch it off" data-testid="automation-mode">
              automation {api.automation ? "on" : "off"}
            </span>
          )}
          {staffTools && api.resetDemo && !demoDeployment && (
            <button className="btn btn-small" onClick={() => api.resetDemo!().then(() => location.reload())}>
              Reset demo
            </button>
          )}
        </div>
        <SignInNotice s={si} compact />
      </div>
    </header>
  );
}
