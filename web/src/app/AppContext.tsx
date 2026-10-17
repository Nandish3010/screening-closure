import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ApiClient, Me, Role } from "../api/contract";
import { ROLES } from "../api/contract";

const ROLE_KEY = "sc.role";

export interface AppState {
  api: ApiClient;
  role: Role;
  setRole: (r: Role) => void;
  me: Me | null;
  /** Display name of the acting user for the current role. */
  actorName: string;
  /** Actor string posted with events, e.g. "coordinator:Maryam Al Suwaidi". */
  actor: string;
  simTime: string | null;
  refreshSim: () => void;
}

const Ctx = createContext<AppState | null>(null);

export function readRole(): Role | null {
  try {
    const r = localStorage.getItem(ROLE_KEY) as Role | null;
    return r && ROLES.includes(r) ? r : null;
  } catch {
    return null;
  }
}

export function AppProvider({ api, children, initialRole }: { api: ApiClient; children: ReactNode; initialRole?: Role }) {
  const [role, setRoleState] = useState<Role>(initialRole ?? readRole() ?? "coordinator");
  const [me, setMe] = useState<Me | null>(null);
  const [simTime, setSimTime] = useState<string | null>(null);

  // Log in as the chosen role (http: POST /auth/dev-login, then the bearer token rides on every request;
  // mock: no-op). Runs on mount and on every role switch; the adapter makes requests wait for it.
  useEffect(() => {
    api.login(role, DEFAULT_NAMES[role]).catch(() => undefined);
  }, [api, role]);

  useEffect(() => {
    let live = true;
    api.me().then((m) => {
      if (!live) return;
      setMe(m);
      // /me supplies the default; a role the demo user picked in the switcher wins.
      if (!initialRole && !readRole()) setRoleState(m.role);
    }).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, initialRole]);

  const refreshSim = useCallback(() => {
    api.closureMetrics().then((m) => setSimTime(m.sim_time)).catch(() => setSimTime(null));
  }, [api]);
  useEffect(refreshSim, [refreshSim]);

  const setRole = useCallback((r: Role) => {
    setRoleState(r);
    try {
      localStorage.setItem(ROLE_KEY, r);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo<AppState>(() => {
    const actorName = me && me.role === role ? me.name : DEFAULT_NAMES[role];
    return { api, role, setRole, me, actorName, actor: `${role}:${actorName}`, simTime, refreshSim };
  }, [api, role, setRole, me, simTime, refreshSim]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const DEFAULT_NAMES: Record<Role, string> = {
  coordinator: "Maryam Al Suwaidi",
  owner: "Dr. Layla Haddad",
  specialist: "Dr. Karim Nasser",
  clinician: "Dr. Huda Rahman",
  patient: "Patient",
};

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside <AppProvider>");
  return v;
}

/** Roles allowed to take each action. Server-side RBAC is out of scope for Cut C. */
export const PERMISSIONS = {
  acknowledge: ["coordinator", "owner"],
  accept: ["coordinator", "owner"],
  contact: ["coordinator", "owner"],
  book: ["coordinator", "owner"],
  handOff: ["coordinator", "owner"],
  escalate: ["coordinator", "owner"],
  draft: ["coordinator", "owner"],
  approve: ["coordinator", "owner"],
  recordResult: ["coordinator", "owner", "specialist"],
  acceptEscalation: ["coordinator", "owner"],
  markLostContact: ["coordinator", "owner"],
  signOff: ["clinician"],
  reopen: ["clinician"],
} as const satisfies Record<string, Role[]>;

export function can(role: Role, action: keyof typeof PERMISSIONS): boolean {
  return (PERMISSIONS[action] as readonly Role[]).includes(role);
}
