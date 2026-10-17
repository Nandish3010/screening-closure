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
  Endpoints that the backend has not shipped yet degrade: `/me` 404 -> coordinator, `/cases/{id}/timeline` 404 -> events from `GET /cases/{id}`, SLA fields borrowed from the `GET /cases` row. Drafts and metrics need the pending endpoints. The API needs CORS for the web origin when run from a different port.

No external network calls at runtime: fonts are bundled (Noto Sans Arabic via `@fontsource`, bundled by Vite), everything else is the system font stack.

## The contract

`src/api/contract.ts` is the single source of truth: every request/response shape, the event vocabulary, payload conventions, and `BACKEND:` notes. Both adapters implement `ApiClient`. Key points for the backend:

- Events the UI posts that are **not** state transitions (`hand_off`, `hand_off_accepted|rejected`, `escalate`, `callback_requested`, `consent_updated`, `draft_generated`, `draft_approved`) must be appended without changing state. Today the engine answers 409 to any non-transition event.
- `POST /cases/{id}/drafts` and `POST /drafts/{id}/approve` should also write `draft_generated` / `draft_approved` events so provenance appears in the timeline; include the draft texts in the `draft_generated` payload.
- `GET /cases?owner=&overdue=true` and the extra summary fields (`owner`, `sla_due_at`, `overdue_days`, `risk_score`). The UI re-filters and re-sorts client side, so a backend that ignores the filters still works.
- Sign-off posts `clinician_review` (only if the case is still at `confirmatory_completed`) then `close` with `{evidence_ref, signed_by}`.

## Roles

Header role switcher (coordinator / owner / specialist / clinician / patient): `GET /me` gives the default, the demo override is stored in `localStorage` (`sc.role`). Permissions are a UI mirror in `src/app/AppContext.tsx` (`PERMISSIONS`); disabled actions always say why. Only the clinician role can sign off. Real RBAC is a backend concern.

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

- `tests/` (Vitest + RTL, jsdom): mock and http adapters, worklist sort/filter, sign-off gating, Arabic toggle (`dir=rtl`, strings swap), `callback_requested`, acknowledge, consent, offline fallback, coordinator actions (escalate -> draft -> approve), i18n completeness.
- `e2e/flows.spec.ts` (Playwright): overdue case -> escalate -> request draft -> approve -> clinician sign-off; patient flow; dashboard + badge on every page; PWA manifest + offline reload.
- Playwright does not need a download if a Chromium headless shell is already in `~/Library/Caches/ms-playwright` (`playwright.config.ts` picks the newest one; override with `PW_CHROMIUM_PATH`). Otherwise `npx playwright install chromium`.
- Screenshots: `SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts` writes `web/screenshots/` (light and dark).
