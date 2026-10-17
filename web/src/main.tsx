import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource/noto-sans-arabic/arabic-400.css";
import "@fontsource/noto-sans-arabic/arabic-700.css";
import App from "./App";
import { AppProvider, DEFAULT_NAMES, readRole } from "./app/AppContext";
import { ROLE_PROVIDERS } from "./api/identities";
import { createClientFromEnv } from "./api";
import "./styles.css";

const api = createClientFromEnv();

// Log in as the remembered (or default) role BEFORE the first render so the first requests carry a token.
// If the backend is down or has no auth, render anyway: pages show their own errors.
const startRole = readRole() ?? "coordinator";
void api.login(startRole, DEFAULT_NAMES[startRole], ROLE_PROVIDERS[startRole]).catch(() => undefined).then(() => {
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <BrowserRouter>
        <AppProvider api={api}>
          <App />
        </AppProvider>
      </BrowserRouter>
    </React.StrictMode>,
  );
});

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}
