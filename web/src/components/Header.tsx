import { useState } from "react";
import { Link, NavLink, matchPath, useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../app/AppContext";
import { ROLES, type Role } from "../api/contract";
import { DEMO_PATIENT_CASE_ID } from "../api/fixtures";
import { fmtDateTime } from "../lib/format";

export function SimBadge() {
  const { simTime } = useApp();
  return (
    <div className="sim-badge" role="status" aria-label="Simulated time" data-testid="sim-badge">
      <span className="sim-label">SIMULATED TIME:</span> <time dateTime={simTime ?? undefined}>{simTime ? fmtDateTime(simTime) : "unavailable"}</time>
    </div>
  );
}

/**
 * Demo-only control for the supervisor role (the only role the API lets move the simulation clock):
 * POST /clock/advance. Not rendered by the mock adapter, whose clock is fixed.
 */
function ClockControl() {
  const { api, refreshSim } = useApp();
  const [days, setDays] = useState(1);
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
        <input type="number" min={1} max={365} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Days to advance the simulation clock" />
      </label>{" "}
      <button className="btn btn-small" onClick={advance} data-testid="advance-clock" disabled={!(days > 0)}>Advance</button>
      {status && <span role="alert" className="why">{status}</span>}
    </span>
  );
}

export function Header() {
  const { role, setRole, api, actorName } = useApp();
  const navigate = useNavigate();
  const { pathname } = useLocation();

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
    <header className="app-header">
      <div className="header-row">
        <Link className="brand" to="/" aria-label="Screening Closure home">
          <span className="brand-mark" aria-hidden="true">&#10003;</span> Screening Closure
        </Link>
        <SimBadge />
      </div>
      <div className="header-row">
        <nav aria-label="Main">
          <NavLink to="/coordinator">Coordinator</NavLink>
          <NavLink to="/dashboard">Dashboard</NavLink>
          {(role === "coordinator" || role === "supervisor") && <NavLink to="/agency">Agency</NavLink>}
          <NavLink to={`/patient/${DEMO_PATIENT_CASE_ID}`}>Patient view</NavLink>
        </nav>
        <div className="header-tools">
          <label className="role-switch">
            <span>Role (demo)</span>
            <select value={role} onChange={(e) => onRole(e.target.value as Role)} aria-label="Switch role">
              {ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>
          <span className="who" title="Acting as">{actorName}</span>
          {role === "supervisor" && <ClockControl />}
          <span className={`adapter adapter-${api.adapter}`} title="API adapter">{api.adapter}</span>
          {api.resetDemo && (
            <button className="btn btn-small" onClick={() => api.resetDemo!().then(() => location.reload())}>
              Reset demo
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
