import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource/noto-sans-arabic/arabic-400.css";
import "@fontsource/noto-sans-arabic/arabic-700.css";
import App from "./App";
import { AppProvider, DEFAULT_NAMES, readRole } from "./app/AppContext";
import { ROLE_PROVIDERS } from "./api/identities";
import { createClientFromEnv } from "./api/client";
import "./styles.css";

const api = await createClientFromEnv();

// Log in as the remembered (or default) role BEFORE the first render so the first requests carry a token.
// If the backend is down or has no auth, render anyway: pages show their own errors.
const startRole = readRole() ?? "coordinator";
// OIDC mode (A29): there is no role to pick; on the redirect back from the provider, swap the code for tokens first.
let signInError: string | null = null;
const ready = api.oidc
  ? api.oidc.handleRedirect(location.search).then(
      (done) => { if (done) history.replaceState({}, "", location.pathname); },
      (e: Error) => { signInError = e.message; history.replaceState({}, "", location.pathname); },
    )
  : api.login(startRole, DEFAULT_NAMES[startRole], ROLE_PROVIDERS[startRole]).catch(() => undefined);
void ready.then(() => {
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <BrowserRouter>
        <AppProvider api={api}>
          <App signInError={signInError} />
        </AppProvider>
      </BrowserRouter>
    </React.StrictMode>,
  );
});

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  // The module now awaits before it gets here (top-level await), so `load` may already have fired: register at once in that case.
  const register = () => void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  if (document.readyState === "complete") register();
  else window.addEventListener("load", register);
}
