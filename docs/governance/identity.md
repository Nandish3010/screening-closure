# Identity: how people sign in (A29, M6, ADR-040)

**Status: built and tested against a local Keycloak that stands in for UAE PASS. It has not been connected to UAE PASS, and what UAE PASS sends (claims, assurance level, Emirates ID handling) is unverified; the list at the end says what must be confirmed first.**

## Two modes

| | `AUTH_MODE=dev` (default) | `AUTH_MODE=oidc` |
|---|---|---|
| Who it is for | A laptop, the mock and the demo | Anything that is not a laptop |
| How a person gets a token | `POST /auth/dev-login {role, name}`, no password | Signs in at the identity provider (authorization code flow with PKCE) |
| Token | HMAC-signed JSON, `exp` 8 hours after login (`DEV_TOKEN_LIFETIME_SECONDS`) | JWT signed by the provider, verified against its JWKS |
| Where the role comes from | The request | The token's realm roles; exactly one of the six |
| Actor in the event log | `role:` + opaque id from role and name (A14) | `role:` + opaque id from role and the provider's `sub` (same function) |
| Dev login | Works only with `APP_ENV=dev` | 404 |
| Service tokens | Unchanged | Unchanged |

Outside `APP_ENV=dev` the API refuses to start unless `AUTH_MODE=oidc` with an `https://` `OIDC_ISSUER` (on top of the checks of ADR-029). A mistyped `AUTH_MODE` fails at start in every environment.

## What the API checks on every request (oidc mode)

`app/oidc.py`, in this order: the header has a `kid` and an RS or ES algorithm (never `none`, never HMAC); the key for that `kid` is in the JWKS (cached 10 minutes; an unknown `kid` causes one refetch, at most every 30 seconds; the last good keys serve for up to an hour if the provider is down); the signature; `iss` equals `OIDC_ISSUER`; `aud` contains `OIDC_AUDIENCE`; `exp` is present and in the future; `nbf` is not in the future; 30 seconds of leeway. Then the role: the token's `realm_access.roles` (path in `OIDC_ROLE_CLAIM`, renames in `OIDC_ROLE_MAP`) must contain exactly one of `lab`, `primary_care_owner`, `specialist_owner`, `coordinator`, `patient`, `supervisor`. A patient token must carry `patient_ref` (`Patient/<id>`); a clinician token may carry `provider`. The display name comes from `name`, `preferred_username` or `email`, is cleaned, and goes to the `users` table only. All failures are a 401 with no detail about which check failed.

Tests: `api/tests/test_oidc.py` (valid, expired, wrong audience, wrong issuer, unknown `kid`, rotated key, `nbf`, algorithm confusion, role mapping, actor stability, fail-closed configuration, dev token expiry, expiry independent of the simulation clock) and `api/tests/test_oidc_keycloak.py` (`make test-oidc`: the committed realm against a real Keycloak; skips without Docker).

## Why token expiry uses the wall clock

Case logic runs on the simulation clock so that it is deterministic and replayable. Token expiry asks whether a credential is still good now, for a real person, and the provider answers it with real time. Moving the demo clock by a year must not log everyone out, and a replay never verifies a token. The wall clock is read in one place for this (`auth.wall_now`); nothing that decides a case's state, deadline or owner reads it. ADR-040 has the argument; it supersedes ADR-012's "tokens never expire".

## Running it locally

```
make oidc-up        # Keycloak 26.0.7 (dev mode) on 127.0.0.1:8180, API restarted with AUTH_MODE=oidc
cd web && VITE_API_ADAPTER=http VITE_AUTH_MODE=oidc VITE_API_URL=http://localhost:8000 npm run dev
make oidc-down      # back to dev mode
```

`make oidc-up` takes each demo password from `.env` (`KC_PW_COORDINATOR`, `KC_PW_OWNER`, `KC_PW_SPECIALIST`, `KC_PW_CLINICIAN`, `KC_PW_SUPERVISOR`, `KC_PW_LAB`, `KC_PW_PATIENT`, `KEYCLOAK_ADMIN_PASSWORD`) or generates one per run and writes them to `.oidc-demo-credentials` (git-ignored, mode 600). The realm file `config/keycloak/screening-closure-realm.json` holds only `${KC_PW_...}` placeholders; Keycloak fills them from its environment at import. The realm has the six roles, one client (`screening-closure-web`, public, PKCE S256, no password grant, redirect URIs on the three local web ports), an audience mapper (`screening-closure`), mappers for `provider`, `patient_ref` and the full name, and seven users mirroring the dev personas (Maryam Al Suwaidi, Dr. Layla Haddad, Dr. Karim Nasser, Dr. Huda Rahman, Dr. Noura Al Hammadi, a lab user and a demo patient). Access tokens last 5 minutes and are renewed with the refresh token; the provider session lasts 8 hours. The demo patient's `patient_ref` is `Patient/demo-patient` unless `DEMO_PATIENT_REF` is set to a seeded patient's reference.

In the web app, oidc mode shows a sign-in page until a session exists; the header's switcher becomes "Sign in as" (it opens the provider's login form for that user, username filled in, and asks for the password) with a "Sign out" button. The mock adapter and dev mode are unchanged. `web/src/api/oidc.ts` is the whole client (discovery, PKCE, state check, code exchange, refresh) and `web/tests/oidc.test.ts` tests it, including the RFC 7636 appendix B challenge.

## How UAE PASS would slot in

UAE PASS is understood to offer an OAuth 2.0 / OpenID Connect authorization code flow (to verify against its documentation, see below), which is what the API and the web client already speak. Moving from Keycloak to it should be configuration plus one mapping module, not new architecture:

1. Register the application with UAE PASS (staging first) to get a client id and the redirect URIs; set `VITE_OIDC_ISSUER`, `VITE_OIDC_CLIENT_ID` and, in the API, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`.
2. Replace the role claim. UAE PASS identifies a person; it does not say that person is a coordinator. Roles would come from our side: either an identity broker in front (Keycloak or the organisation's IdP federating UAE PASS and adding roles from a directory), or an assignment table keyed by `sub`. The realm file shows the first shape. Do not map roles from anything the person can edit.
3. Settle which identifier is `sub`. The opaque actor is a hash of the role and `sub`, so `sub` must be stable and must not be reused. If UAE PASS gives a per-client pairwise `sub`, that is fine; if the organisation moves to another client id the actors change, which is a migration to plan.
4. Do not copy Emirates ID (or any national identifier) into tokens, logs, the users table or the event log; the display name only. Whether the claim is needed at all is a question for the data-protection lead.
5. The patient surface needs a way to bind a UAE PASS login to a `patient_ref` (the realm does it with an attribute). That binding is an enrolment decision, not a token one.

## What is still missing or unverified

- **UAE PASS itself.** Not integrated. To verify with the operator and the UAE PASS documentation: the exact issuer, the claims it returns, whether the access token is a JWT the API can verify (the API needs one; an opaque token would need introspection), the assurance levels, and the sandbox terms. Nothing in this repository claims UAE PASS behaviour.
- **MFA policy.** Keycloak is configured for passwords only. Which roles need a second factor, and whether UAE PASS's own assurance level satisfies it, is a policy decision for the provider's security lead.
- **Emirates ID claims mapping.** Which claims a UAE PASS login carries for a resident or citizen, which of them may be stored, and how a patient is bound to a `patient_ref` are unverified (to verify).
- **Revocation.** A token is good until `exp` (5 minutes for the realm's access tokens). No introspection, no revocation list, no back-channel logout. "Sign out" forgets the tokens in the browser; it does not end the provider session.
- **Browser token storage.** Tokens are in `sessionStorage` (gone when the tab closes, readable by script on the page). A backend-for-frontend with an httpOnly cookie would remove that exposure.
- **Re-route targets in oidc mode.** The case page's re-route picker needs another person's opaque id, which the dev adapter learns by logging in as them. With real identities there is no such lookup; a directory endpoint for staff (not patients) would be needed.
- **Owner scoping and provisioning.** Roles are coarse (ADR-012). Who creates, disables and reviews accounts is an operating procedure that does not exist yet.
- **Keycloak here is a development tool** (`start-dev`, plain HTTP, built-in database in the container). It is not a deployment recipe.
