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

- **mock**: the whole API in the browser, seeded from `src/api/fixtures.ts` (12 synthetic cases, sim clock fixed at 2026-11-04 09:00 UTC). It mirrors the engine's rules (state transitions, closure needs `evidence_ref` + `signed_by`, no automated closer) and persists to `localStorage`, so the coordinator and patient views in two tabs see each other. Header button "Reset demo" restores the seed. This is the offline demo fallback.
- **http**: the real API at `VITE_API_URL` (default `http://localhost:8000`):
  `VITE_API_ADAPTER=http VITE_API_URL=http://localhost:8000 npm run dev`.
  Endpoints that the backend has not shipped yet degrade: `/me` 404 -> coordinator, `/cases/{id}/timeline` 404 -> events from `GET /cases/{id}`, `POST /auth/dev-login` 404 -> no token. Read-model fields a row lacks (`owner`, `due_at`, `overdue`, `escalation_level`, `consent_frozen`, `last_event_at`) get safe defaults, or are derived from the event log on `GET /cases/{id}`. Drafts and metrics need the pending endpoints. The API needs CORS for the web origin (including the `Authorization` header) when run from a different port.

### Auth (http adapter)

The backend requires `Authorization: Bearer <token>`. The token comes from `POST /auth/dev-login {role, name}` (response `{access_token, token_type}`; `{token}` is also accepted). The http adapter logs in before the first render (as the remembered role, default coordinator), **again on every role switch in the header**, and once more if a request gets a 401 (then it retries that request once). Requests issued during a switch wait for the new token. The names used are the demo users in `AppContext.tsx` (`DEFAULT_NAMES`); the actor string posted with events is `role:name`. The mock adapter ignores auth (`login` is a no-op).

No external network calls at runtime: fonts are bundled (Noto Sans Arabic via `@fontsource`, bundled by Vite), everything else is the system font stack.

## The contract

`src/api/contract.ts` is the single source of truth: every request/response shape, the event vocabulary, payload conventions, and `BACKEND:` notes. Both adapters implement `ApiClient`. Key points for the backend (event names and payloads follow the engine's canonical vocabulary, ticket A2):

- **Events the UI posts** that are not main-path transitions must be appended without changing state: `handoff_requested {to_provider}`, `escalate {rung: reminder|supervisor|coordinator, target_role}`, `escalation_accepted {rung}` (payload not specified in the ticket; the UI sends the rung), `callback_requested`, `consent_updated`, `mark_lost_contact {}`, and `reopen {reason}` (clinician only, closed cases only).
- **Events the backend writes** and the UI only displays: `handoff_accepted`, `handoff_rejected {reason: capacity|wrong_service|missing_info|patient_declined}`, `message_drafted {draft_id, provider, fallback_used, text_ar, text_en}`, `message_approved {draft_id}` followed by `message_sent {draft_id}`, `mark_overdue {state, due_at}`, `watchdog_flagged {reason: idle|scheduler_down, idle_days?}`. `POST /cases/{id}/drafts` writes `message_drafted` (with the texts, so a pending draft survives a reload); `POST /drafts/{id}/approve` writes `message_approved`, then the backend emits `message_sent`. The UI says "approved, not sent yet" until `message_sent` arrives.
- **Read model** on `GET /cases` and `GET /cases/{id}`: `owner`, `due_at` (was `sla_due_at`), `overdue` (bool), `escalation_level` (0-3), `consent_frozen` (bool), `last_event_at`, optional `risk_score`. `overdue_days` is gone: the UI computes it from `due_at` and the sim clock. `GET /cases?owner=&overdue=true` filters are optional (the UI re-filters and re-sorts client side).
- **Asked of the backend, not in the A2 list**: an optional `watchdog_flagged: {reason, idle_days?, at}` on the `GET /cases` rows, so the worklist can show the red "Nobody is looking at this" marker. Without it the worklist shows no marker; the case page derives it from the events either way.
- **Actors** are `role:name` (`coordinator:amal`, `clinician:dr_ali`, `system:scheduler`, `model:drafter`) or a bare `watchdog`. `system`, `model`, `watchdog` and `scheduler` are shown with an "automated" chip. Only the clinician role can sign off or reopen; no automated actor can close.
- Sign-off posts `clinician_review` (only if the case is still at `confirmatory_completed`) then `close` with `{evidence_ref, signed_by}`.

### What the UI shows for the new vocabulary

- **Overdue**: the worklist and case page use `overdue` + `due_at`. If a server omits `overdue`, `GET /cases/{id}` derives it from the latest `mark_overdue` that no later state change has superseded.
- **Watchdog**: `watchdog_flagged` renders as a red "Nobody is looking at this" chip in the worklist, a banner on the case page and a red timeline entry. It clears when a state change or a human event follows.
- **Escalation**: `escalation_level` shows as "Level N" in the worklist. An `escalate` stays "waiting for a human" until `escalation_accepted`; the case page has an Accept escalation button (coordinator/owner).
- **Consent frozen**: chip in the worklist, banner on the case page; "Record patient contacted" and the draft buttons are disabled with the reason. (The mock freezes consent on a `consent_updated` with `withdrawn: true`; against the real backend `consent_frozen` is authoritative.)
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
