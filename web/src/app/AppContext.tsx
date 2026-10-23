import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ApiClient, Me, OutreachStatus, Role } from "../api/contract";
import { BACKEND_ROLE, OWNER_SCOPED_ROLES, ROLES } from "../api/contract";
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
  /** The API's opaque `user_id` for the acting login (http only; the real API's actor is `role:user_id`). */
  userId: string | undefined;
  simTime: string | null;
  refreshSim: () => void;
  /** `GET /health` -> `outreach` (A26): null until read, or on a server that does not report it. */
  outreach: OutreachStatus | null;
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
  const [outreach, setOutreach] = useState<OutreachStatus | null>(null);

  // Log in as the chosen role (http: POST /auth/dev-login, then the bearer token rides on every request;
  // mock: no-op). Runs on mount and on every role switch; the adapter makes requests wait for it.
  useEffect(() => {
    api.login(role, DEFAULT_NAMES[role], ROLE_PROVIDERS[role]).catch(() => undefined);
  }, [api, role]);

  useEffect(() => {
    let live = true;
    setMe(null); // a stale /me from the previous role must not name the new one
    api.me().then((m) => {
      if (!live) return;
      setMe(m);
      // /me supplies the default; a role the demo user picked in the switcher wins.
      if (!initialRole && !readRole()) setRoleState(m.role);
    }).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, initialRole, role]);

  const refreshSim = useCallback(() => {
    // /health is public and carries the sim clock; /metrics/closure is staff-only, so a clinician or patient would get a 403.
    api.health().then((h) => {
      setOutreach(h.outreach ?? null);
      return h.sim_time ? setSimTime(h.sim_time) : api.closureMetrics().then((m) => setSimTime(m.sim_time));
    }).catch(() => setSimTime(null));
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
    // Against the real API the actor is `role:user_id` (opaque id, A14): compared with `owner.actor` to tell "mine".
    const userId = api.adapter === "http" && me && me.role === role ? me.user_id : undefined;
    const actor = userId ? `${BACKEND_ROLE[role]}:${userId}` : backendActor(role, actorName);
    return { api, role, setRole, me, actorName, actor, provider: ROLE_PROVIDERS[role], userId, simTime, refreshSim, outreach };
  }, [api, role, setRole, me, simTime, refreshSim, outreach]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const DEFAULT_NAMES = ROLE_NAMES;

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside <AppProvider>");
  return v;
}

const OWNERS = ["owner", "specialist", "clinician"] as const;
const STAFF_ESCALATORS = ["coordinator", "supervisor"] as const;

/**
 * Roles allowed to take each action: a UI mirror of api/app/policy.py EVENT_POLICY and ROUTE_POLICY (the API is the
 * authority; a hidden or disabled action is also refused there). "owner", "specialist" and "clinician" are the
 * backend's clinician roles (primary_care_owner / specialist_owner), who act only on cases they own (A5).
 */
export const PERMISSIONS = {
  acknowledge: ["coordinator"],
  accept: OWNERS,
  contact: ["coordinator", "supervisor", ...OWNERS],
  book: ["coordinator", ...OWNERS],
  handOff: OWNERS,
  answerHandoff: OWNERS,
  reassign: ["coordinator", "supervisor"],
  escalate: STAFF_ESCALATORS,
  draft: ["coordinator", ...OWNERS],
  /** Approving or rejecting a draft is the case owner's decision. Never the coordinator. */
  decideDraft: OWNERS,
  recordResult: ["specialist"],
  /** The supervisor rung is accepted by a supervisor, the coordinator rung by a coordinator, the reminder by the owner (the API decides). */
  acceptEscalation: ["coordinator", "supervisor", ...OWNERS],
  markLostContact: ["coordinator", "supervisor", ...OWNERS],
  /** Record the colonoscopy as FHIR evidence (E8): the case owner (the API checks ownership). */
  recordColonoscopy: OWNERS,
  recordConsent: ["coordinator"],
  /** Record what the patient wrote back (D13): the coordinator (the API route is coordinator-only). */
  recordReply: ["coordinator"],
  signOff: OWNERS,
  /** A33: record an informed refusal (the counsellor is named in the payload and must be a physician). */
  recordRefusal: ["coordinator", ...OWNERS],
  /** A33: sign a declined closure: a physician who owns the case and did not counsel the patient. */
  signDeclined: OWNERS,
  /** A34: cancel a booked appointment (a rebook is `book`). */
  cancelAppointment: ["coordinator", ...OWNERS],
  reopen: OWNERS,
} as const satisfies Record<string, readonly Role[]>;

export function can(role: Role, action: keyof typeof PERMISSIONS): boolean {
  return (PERMISSIONS[action] as readonly Role[]).includes(role);
}

/** Clinician logins act only on cases they own (A5). */
export const isOwnerScoped = (role: Role): boolean => OWNER_SCOPED_ROLES.includes(role);
