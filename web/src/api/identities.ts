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
  supervisor: "Dr. Noura Al Hammadi",
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

/** The owners a coordinator can re-route a bounced or rejected referral to (demo list); `reassign.owner` needs their actor string. */
export const REASSIGN_TARGETS: readonly Role[] = ["owner", "specialist", "clinician"];

/**
 * The demo users of the local Keycloak realm (config/keycloak/screening-closure-realm.json), what the header's
 * "Sign in as" lists in oidc mode (A29). `username` pre-fills the provider's login form (`login_hint`); the password is
 * generated per run (`make oidc-up`, .oidc-demo-credentials) and never appears in the app.
 */
export const DEMO_USERS: ReadonlyArray<{ role: Role; name: string; username: string }> = [
  { role: "coordinator", name: ROLE_NAMES.coordinator, username: "maryam.alsuwaidi" },
  { role: "owner", name: ROLE_NAMES.owner, username: "layla.haddad" },
  { role: "specialist", name: ROLE_NAMES.specialist, username: "karim.nasser" },
  { role: "clinician", name: ROLE_NAMES.clinician, username: "huda.rahman" },
  { role: "supervisor", name: ROLE_NAMES.supervisor, username: "noura.alhammadi" },
  { role: "patient", name: "Demo Patient", username: "demo.patient" },
];

/**
 * The four logins a judge is given on the hosted demo (deploy/gcp/credentials.sh prints the passwords once, for the submission).
 * Only usernames and plain-language descriptions live here: a password is a deploy-time secret in Secret Manager, and a web bundle
 * is public, so none can be embedded and the sign-in page never offers one. The cards pre-fill the username (`login_hint`) and the
 * identity provider asks for the password. Shown only when `GET /health` reports `demo.deployment`.
 */
export const JUDGE_ROLES: ReadonlyArray<{ role: Role; title: string; username: string; does: string; tryThis: string }> = [
  { role: "coordinator", title: "Coordinator", username: "maryam.alsuwaidi",
    does: "Runs the worklist: acknowledges results, books, reassigns, escalates.", tryThis: "Start here: the Today screen and the worklist." },
  { role: "clinician", title: "Clinician (case owner)", username: "huda.rahman",
    does: "Owns cases: accepts, refers on, approves messages, signs the closure.", tryThis: "Open a case and use the sign-off panel." },
  { role: "supervisor", title: "Supervisor", username: "noura.alhammadi",
    does: "Sees the dashboard and agency inbox, and moves demo time forward.", tryThis: "Use Advance in the header, then watch the safety net." },
  { role: "patient", title: "Patient", username: "demo.patient",
    does: "The patient's own app, in Arabic or English, with the privacy centre.", tryThis: "See the next step, the appointment and who looked at the record." },
];
