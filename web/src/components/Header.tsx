import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
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

export function Header() {
  const { role, setRole, api, actorName } = useApp();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const onRole = (r: Role) => {
    setRole(r);
    if (r === "patient") navigate(`/patient/${DEMO_PATIENT_CASE_ID}`);
    else if (pathname.startsWith("/patient")) navigate("/coordinator");
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
