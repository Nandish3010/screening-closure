/**
 * The demo logins behind the header's role switcher, shared by the app (AppContext) and the mock adapter.
 * Names are synthetic. The providers are the organisations the clinician logins act for (sent to
 * `POST /auth/dev-login` as `provider`); they decide which handoff requests a clinician may answer (A5).
 */
import type { Role } from "./contract";
import { BACKEND_ROLE } from "./contract";

export const ROLE_NAMES: Record<Role, string> = {
  coordinator: "Maryam Al Suwaidi",
  owner: "Dr. Layla Haddad",
  specialist: "Dr. Karim Nasser",
  clinician: "Dr. Huda Rahman",
  patient: "Patient",
};

/** The provider each clinician login acts for. Coordinators and patients have none. */
export const ROLE_PROVIDERS: Partial<Record<Role, string>> = {
  owner: "Riverside Primary Care",
  specialist: "Corniche Gastro Unit",
  clinician: "Marina Digestive Health",
};

/** The clinician who answers for each provider when the mock plays the receiving side of a handoff. */
export const PROVIDER_CLINICIANS: Record<string, { role: "primary_care_owner" | "specialist_owner"; name: string }> = {
  "Al Noor Endoscopy Centre": { role: "specialist_owner", name: "Dr. Rana Farouk" },
  "Corniche Gastro Unit": { role: "specialist_owner", name: "Dr. Karim Nasser" },
  "Marina Digestive Health": { role: "primary_care_owner", name: "Dr. Huda Rahman" },
  "Riverside Primary Care": { role: "primary_care_owner", name: "Dr. Layla Haddad" },
};

/** `role:name` as the backend knows a login: the actor of everything that login posts. */
export function backendActor(role: Role, name: string): string {
  return `${BACKEND_ROLE[role]}:${name}`;
}
