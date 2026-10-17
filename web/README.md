# Web (Cut C: interfaces)

Vite + React + TypeScript. Three surfaces on one typed API client:

| Route | Surface |
|---|---|
| `/coordinator`, `/coordinator/:caseId` | Coordinator / owner app: worklist (most overdue first, then risk), filters, case timeline, actions |
| `/patient/:caseId` (and `/patient`) | Patient PWA, Arabic + English, installable, last case renders offline |
| `/dashboard` | Closure dashboard: P50/P90, open by age, breaches by provider, bottleneck by transition |

Every page shows a permanent **SIMULATED TIME** badge in the header (from `sim_time` in `GET /metrics/closure`).
All data is synthetic. Nothing here diagnoses anyone; the AI draft flow is draft -> human approval, and the risk score only orders the worklist.

## Run

```
make web-install        # npm install
make web-dev            # http://localhost:5173, mock adapter by default
make web-build          # tsc + vite build -> web/dist
make web-test           # vitest: unit + React Testing Library
make web-e2e            # Playwright (headless, mock adapter via `vite preview`)
```

### Mock vs http adapter

`VITE_API_ADAPTER=mock|http` (build/dev time; default `mock`). See `.env.example`.

- **mock**: the whole API in the browser, seeded from `src/api/fixtures.ts` (16 synthetic cases, sim clock fixed at 2026-11-04 09:00 UTC). It mirrors the engine's rules (state transitions, closure needs `evidence_ref` + `signed_by`, no automated closer, the referral handoff contract with owner scoping, the consent ledger and freeze, the draft approval gate and the notification mock's consent/contact blocks) and persists to `localStorage`, so the coordinator and patient views in two tabs see each other. `login()` records who is acting, because the real API decides who may approve a draft from the bearer token. Header button "Reset demo" restores the seed. This is the offline demo fallback.
- **http**: the real API at `VITE_API_URL` (default `http://localhost:8000`):
  `VITE_API_ADAPTER=http VITE_API_URL=http://localhost:8000 npm run dev`.
  Endpoints that the backend has not shipped yet degrade: `/me` 404 -> coordinator, `/cases/{id}/timeline` 404 -> events from `GET /cases/{id}`, `GET /alerts` 404 -> no alerts, `POST /auth/dev-login` 404 -> no token. Read-model fields a row lacks (`owner`, `due_at`, `overdue`, `escalation_level`, `consent_frozen`, `last_event_at`, `provider`, `pending_handoff`, `last_handoff`, `consent`) get safe defaults, or are derived from the event log on `GET /cases/{id}`; the real `GET /cases/{id}` nests them under `read_model`, which the adapter lifts. The owner may be a string (actor or name) or the A14 object `{actor, display_name, role}`. The metrics endpoint is still pending. The API needs CORS for the web origin (including the `Authorization` header) when run from a different port.

### Auth (http adapter)

The backend requires `Authorization: Bearer <token>`. The token comes from `POST /auth/dev-login {role, name, provider?}` (response `{token, token_type, user_id, role, display_name, provider?}`; `{access_token}` is also accepted). `role` is a **backend** role: the UI's roles map by `BACKEND_ROLE` in `contract.ts` (`owner` and `clinician` log in as `primary_care_owner`, `specialist` as `specialist_owner`, `coordinator` and `patient` as themselves). `provider` is sent for the clinician roles only: it is the organisation they act for and decides which handoff requests they may answer. The http adapter logs in before the first render (as the remembered role, default coordinator), **again on every role switch in the header**, and once more if a request gets a 401 (then it retries that request once). Requests issued during a switch wait for the new token. The names and providers are the demo logins in `src/api/identities.ts`. The actor of every event is the token's identity: `POST /cases/{id}/events` answers 422 to a body that contains `actor`, so the http adapter never sends one (the mock uses the actor the UI passes, `role:name` with a backend role).

No external network calls at runtime: fonts are bundled (Noto Sans Arabic via `@fontsource`, bundled by Vite), everything else is the system font stack.

## The contract

`src/api/contract.ts` is the single source of truth: every request/response shape, the event vocabulary, payload conventions, and `BACKEND:` notes. Both adapters implement `ApiClient`. Key points for the backend (event names and payloads follow the engine's canonical vocabulary, ticket A2):

- **Events the UI posts** that are not main-path transitions must be appended without changing state: `handoff_requested {to_provider}`, `handoff_accepted` / `handoff_rejected {reason}` (the receiving clinician), `reassign {owner}` (coordinator, from `referral_rejected`), `escalate {rung: reminder|supervisor|coordinator, target_role}`, `escalation_accepted {rung}`, `callback_requested`, `consent_updated` (any non-empty subset of the six booleans `channel_sms`, `channel_whatsapp`, `channel_phone`, `channel_letter`, `share_with_specialist`, `withdrawn`), `mark_lost_contact {}`, and `reopen {reason}` (a clinician, closed cases only).
- **Events the backend writes** and the UI only displays: `handoff_rejected {reason: timeout, bounced: true}` (the scheduler's bounce after 3 working days), `message_drafted {draft_id, provider, fallback_used, prompt_hash, language}` (**no text**), `message_approved {draft_id}` followed by `message_sent {draft_id, notification_id, channel, language, purpose}`, `review_requested {reason: consent_withdrawn}`, `mark_overdue {state, due_at}`, `watchdog_flagged {reason: idle|scheduler_down, idle_days?}`.
- **Drafts** (D4, D5): `POST /cases/{id}/drafts {purpose, channel, language}` -> `{draft_id, text, text_ar, text_en (the other is null), language, provider, fallback_used, abstention_reason, prompt_hash}`. The draft text is only on `GET /cases/{id}/drafts`, so both the timeline and the drafts panel load it there (an older `message_drafted` that still carries text is tolerated). `POST /drafts/{id}/approve` has no body; the answer `{draft_id, approved_by, approved_at, sent, blocked_reason, notification_id}` is shown as "Sent" or "Approved but blocked: consent | contact | case_state". `POST /drafts/{id}/reject {reason}` is recorded on the draft only (no event until A16). Only the owner of the case (or a supervisor) decides; a coordinator can request but not approve, and the panel says why.
- **Read model** on `GET /cases` and `GET /cases/{id}`: `owner` (string, actor, or the A14 object `{actor, display_name, role}`; normalised to `owner` / `owner_actor` / `owner_role`), `due_at` (was `sla_due_at`; `breach_due_at` is accepted), `overdue`, `escalation_level` (0-3), `consent_frozen`, `last_event_at`, `provider`, `pending_handoff {to_provider, requested_by, from_owner, requested_at}`, `last_handoff {outcome, to_provider, by, at, reason?, bounced?}`, optional `risk_score`; on the detail also `consent`, `consent_history`, `review_reason`. `overdue_days` is gone: the UI computes it from `due_at` and the sim clock. Filters `?state=&owner=&overdue=true&flagged=true` are optional (the UI re-filters and re-sorts client side).
- **Asked of the backend**: `watchdog_flagged: {reason, idle_days?, at} | null` on the `GET /cases` rows (worklist marker and the dashboard panel; `?flagged=true`); `GET /alerts -> [{kind: "scheduler_down", since, cleared_at}]` (dashboard banner); `GET /cases/{id}/timeline` items `{kind, at, ...}` with kinds `event | sla_started | sla_breached | escalation | bounce | watchdog` (field names beyond `kind` and `at` are a guess, every renderer treats them as optional).
- **Actors** are `role:name` with a backend role (`coordinator:amal`, `primary_care_owner:dr_ali`, `specialist_owner:dr_s`, `system:scheduler`, `system:notifier`, `system:consent`, `model:template`) or a bare `watchdog`. `system`, `model`, `watchdog` and `scheduler` are shown with an "automated" chip. Only a clinician (an owner role) signs off or reopens; no automated actor can close.
- **Owner scoping (A5)**: a clinician acts only on a case they own. On someone else's case the UI disables the action with the reason and offers "Request handoff to my provider" (`handoff_requested {to_provider: <their provider>}`), then shows "Waiting for acceptance" from `pending_handoff`. **Guess**: the backend today answers 403 to that request (only the owner or a supervisor may post `handoff_requested`); the UI shows the engine's reason when it does. The clinician at the receiving provider gets Accept / Decline buttons.
- Sign-off posts `clinician_review` (only if the case is still at `confirmatory_completed` or `reopened`) then `close` with `{evidence_ref, signed_by}`.

### What the UI shows for the new vocabulary

- **Overdue**: the worklist and case page use `overdue` + `due_at`. If a server omits `overdue`, `GET /cases/{id}` derives it from the latest `mark_overdue` that no later state change has superseded.
- **Watchdog**: `watchdog_flagged` renders as a red "Nobody is looking at this" chip in the worklist, a banner on the case page and a red timeline entry. It clears when a state change or a human event follows.
- **Escalation**: `escalation_level` shows as "Level N" in the worklist. An `escalate` stays "waiting for a human" until `escalation_accepted`; the case page has an Accept escalation button (coordinator/owner).
- **Consent frozen**: chip in the worklist, banner on the case page (with "flagged for human review" when `review_reason` is set); "Record patient contacted", the draft request and approval, and new hand-offs are disabled with the reason. Rejecting a draft stays possible. (The mock freezes consent on `consent_updated {withdrawn: true}` and adds `review_requested` as `system:consent`; against the real backend `consent_frozen` is authoritative.) A coordinator can record the patient's withdrawal or restoration from the case page.
- **Hand-offs**: "Waiting for acceptance" (banner and panel) while `pending_handoff` is set; a scheduler bounce (`last_handoff.bounced`, state `referral_rejected`) is a separate red banner, "Hand-off bounced: no response in 3 working days", with the re-route action for a coordinator (`reassign`); ordinary rejections keep the taxonomy label.
- **Reopen**: shown on closed cases only; enabled for the clinician role and needs a reason. **Mark lost contact**: coordinator/owner, any open case.
- **Hand-off rejections** show the taxonomy reason as a label ("No capacity (no slot in time)"); an unknown code is shown as-is.

## Roles

Header role switcher (coordinator / owner / specialist / clinician / patient): `GET /me` gives the default, the demo override is stored in `localStorage` (`sc.role`) and switching logs in again (see Auth). Permissions are a UI mirror in `src/app/AppContext.tsx` (`PERMISSIONS`); disabled actions always say why. Only the clinician role can sign off. Real RBAC is a backend concern.

## Patient page, i18n and RTL

- Strings: `src/i18n/strings.ts`. **Every Arabic string is marked `TODO: native-speaker review`**; they were written by engineering and must be reviewed before use with patients. English is written at roughly grade-6 level (short sentences).
- The toggle sets `lang` and `dir` on the page root and on `<html>` (restored on leave). Names are wrapped in bidi isolates. CSS uses logical properties (`inline-start`, `padding-inline-start`).
- "I understand" posts `patient_acknowledge` (only enabled when the case is at `patient_contacted`, the only state where the engine accepts it). "I don't understand this" posts `callback_requested`, which shows as a banner on the coordinator's case page. Consent changes post `consent_updated` immediately.

## PWA notes

- `public/manifest.webmanifest` (`start_url: /patient`) and `public/sw.js`, registered only in production builds. Test with `npm run build && npm run preview`.
- The service worker precaches the shell and caches same-origin static assets at runtime; navigations are network-first with the cached `index.html` as the offline fallback, so any `/patient/:caseId` route boots offline. API responses are deliberately not cached by the service worker. The patient page keeps the **last loaded case in `localStorage`** and renders it with an "offline" banner when the API is unreachable.
- Arabic font files are cached the first time Arabic is shown; before that, offline Arabic uses the system font.
- Actions taken offline are not queued (the patient sees "That did not work").

## Tests

- `tests/` (Vitest + RTL, jsdom): mock and http adapters, worklist sort/filter (days overdue computed from `due_at`), derivations (actors, overdue marker, watchdog, escalation, drafts, hand-offs), http auth (login, token swap on role switch, 401 retry), sign-off gating, Arabic toggle (`dir=rtl`, strings swap), `callback_requested`, acknowledge, consent, offline fallback, coordinator actions (escalate -> draft -> approve -> sent, accept escalation, watchdog and automated chips, frozen consent, mark lost contact, reopen), i18n completeness.
- `e2e/flows.spec.ts` (Playwright): overdue case -> escalate -> request draft -> approve -> sent -> clinician sign-off; watchdog marker; clinician-only reopen; patient flow; dashboard + badge on every page; PWA manifest + offline reload.
- Playwright does not need a download if a Chromium headless shell is already in `~/Library/Caches/ms-playwright` (`playwright.config.ts` picks the newest one; override with `PW_CHROMIUM_PATH`). Otherwise `npx playwright install chromium`.
- Screenshots: `SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts` writes `web/screenshots/` (light and dark).
