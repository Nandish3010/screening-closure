# Web (PWA stub)

Status: not scaffolded yet.

Planned: Vite + React progressive web app with two faces, a staff/coordinator view
(case list, overdue, escalations, closure sign-off) and a patient view. Arabic and
English with full RTL support are planned for the patient view.

To scaffold later (not run in Week 1):

```
npm create vite@latest . -- --template react
```

then add a PWA plugin (e.g. `vite-plugin-pwa`) and point the dev proxy at the API
on `http://localhost:8000`.
