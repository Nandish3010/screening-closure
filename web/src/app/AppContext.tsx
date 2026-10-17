import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ApiClient, Me, Role } from "../api/contract";
import { OWNER_SCOPED_ROLES, ROLES } from "../api/contract";
import { ROLE_NAMES, ROLE_PROVIDERS, backendActor } from "../api/identities";

const ROLE_KEY = "sc.role";

export interface AppState {
  api: ApiClient;
  role: Role;
  setRole: (r: Role) => void;
  me: Me | null;
  /** Display name of the acting user for the current role. */
  actorName: string;
  /**
   * The acting login as the backend knows it (`role:name` with a BACKEND role), e.g.
   * "primary_care_owner:Dr. Layla Haddad". The mock uses it as the actor of what the UI posts; the real API
   * derives the actor from the bearer token and ignores it. Compared with the case owner's actor.
   */
  actor: string;
  /** The provider this clinician login acts for (owner roles), else undefined. */
  provider: string | undefined;
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
    api.login(role, DEFAULT_NAMES[role], ROLE_PROVIDERS[role]).catch(() => undefined);
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
    return { api, role, setRole, me, actorName, actor: backendActor(role, actorName), provider: ROLE_PROVIDERS[role], simTime, refreshSim };
  }, [api, role, setRole, me, simTime, refreshSim]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const DEFAULT_NAMES = ROLE_NAMES;

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside <AppProvider>");
  return v;
}

const OWNERS = ["owner", "specialist", "clinician"] as const;

/**
 * Roles allowed to take each action: a UI mirror of api/app/policy.py EVENT_POLICY and ROUTE_POLICY (the API is the
 * authority; a hidden or disabled action is also refused there). "owner", "specialist" and "clinician" are the
 * backend's clinician roles (primary_care_owner / specialist_owner), who act only on cases they own (A5).
 */
export const PERMISSIONS = {
  acknowledge: ["coordinator"],
  accept: OWNERS,
  contact: ["coordinator", ...OWNERS],
  book: ["coordinator", ...OWNERS],
  handOff: OWNERS,
  answerHandoff: OWNERS,
  reassign: ["coordinator"],
  escalate: ["coordinator"],
  draft: ["coordinator", ...OWNERS],
  /** Approving or rejecting a draft is the case owner's decision. Never the coordinator. */
  decideDraft: OWNERS,
  recordResult: ["specialist"],
  acceptEscalation: ["coordinator", ...OWNERS],
  markLostContact: ["coordinator", ...OWNERS],
  recordConsent: ["coordinator"],
  signOff: OWNERS,
  reopen: OWNERS,
} as const satisfies Record<string, readonly Role[]>;

export function can(role: Role, action: keyof typeof PERMISSIONS): boolean {
  return (PERMISSIONS[action] as readonly Role[]).includes(role);
}

/** Clinician logins act only on cases they own (A5). */
export const isOwnerScoped = (role: Role): boolean => OWNER_SCOPED_ROLES.includes(role);
